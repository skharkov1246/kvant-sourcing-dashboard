// Общая строка поиска (public/portal_search.js) на придуманных ответах.
// Браузера здесь нет — мини-DOM (minidom.mjs) плюс три узла, которые нужны
// именно этому скрипту: он вставляет полоску ПЕРЕД содержимым страницы.
//
// Что проверяется по существу, а не «не упало»:
//   · на страницах раздела полоска встаёт сверху, а свои ссылки страницы
//     остаются; на стартовой странице строка рисуется в отведённом месте;
//   · выдача сгруппирована по видам, у кода две колонки рядом — «Код» и
//     «Бренд», каждая строка ведёт на существующую страницу своим адресом;
//   · бренд без ключа реестра — словом, а не брендом; библиотека без права —
//     без ссылки, и это сказано;
//   · строка из базы разметкой не становится (innerHTML не используется);
//   · отказ базы и «не успели» — словами, а не пустотой.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { Element, потомки } from "./minidom.mjs";

const source = fs.readFileSync(new URL("../../public/portal_search.js", import.meta.url), "utf8");

// Узлы настоящего DOM, которых мини-DOM не знает. Здесь, а не в minidom.mjs:
// нужны только этому скрипту.
Element.prototype.insertBefore = function (node, ref) {
  const i = ref ? this.children.indexOf(ref) : -1;
  if (i < 0) this.children.push(node); else this.children.splice(i, 0, node);
  return node;
};
Object.defineProperty(Element.prototype, "firstChild", { configurable: true,
  get() { return this.children[0] || null; } });
Element.prototype.contains = function (node) { return потомки(this).includes(node); };

const ОТВЕТ = {
  q: "AB-6205", library: true, partial: [],
  rows: [
    { kind: "код", key: "ab6205", title: "AB-6205", subtitle: "Подшипник выдуманный", brand: "SKF",
      brand_key: "skf", brand_src: "частота", counts: { deals: 2, offers: 3, suppliers: 2 }, source: "спрос · КП", rank: 0 },
    { kind: "код", key: "ab6205zz", title: "<img src=x onerror=alert(1)>", subtitle: null,
      brand: "Выдуманный литейщик", brand_key: null, brand_src: "написание", counts: { deals: 1 }, source: "спрос", rank: 1 },
    { kind: "поставщик", key: "KV-S-000011-1", title: "Альфа-Подшипник", subtitle: "KV-S-000011-1 · ИНН 7700000001",
      counts: { offers: 3, codes: 2 }, source: "ИНН", rank: 1 },
    { kind: "бренд", key: "skf", title: "SKF", subtitle: null, counts: { spellings: 3, rows: 12 }, rank: 1 },
    { kind: "машина", key: "vm400", title: "ВМ-400", subtitle: "турбина", segment: "gtu",
      counts: { parts: 2, fleet: 1 }, rank: 2 },
    { kind: "узел", key: "hot.liner", title: "Жаровая труба", subtitle: "Горячая часть", counts: { parts: 2 }, rank: 2 },
  ],
};

function окружение({ место = false, путь = "/suppliers", ответ = () => [200, ОТВЕТ] } = {}) {
  const body = new Element("body");
  const шапка = new Element("nav");
  шапка.id = "topbar";
  const назад = new Element("a");
  назад.href = "/";
  шапка.appendChild(назад);
  body.appendChild(шапка);
  if (место) { const m = new Element("div"); m.id = "kvps"; body.appendChild(m); }
  const head = new Element("head");
  const созданные = [];
  const запросы = [];
  const location = { pathname: путь, hash: "", href: "", reloaded: 0, reload() { this.reloaded += 1; } };
  const document = {
    body, head,
    getElementById: (id) => потомки(body).find((e) => e.id === id || e.attrs.id === id) || null,
    createElement: (tag) => { const e = new Element(tag); созданные.push(e); return e; },
    addEventListener: () => {},
  };
  const context = {
    document, location, encodeURIComponent, setTimeout, clearTimeout, AbortController,
    fetch: (url) => {
      запросы.push(String(url));
      const [status, value] = ответ(String(url));
      return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  const input = потомки(body).find((e) => e.tagName === "INPUT");
  const out = потомки(body).find((e) => e.attrs.id === "kvps-out");
  return { body, head, input, out, запросы, созданные, location };
}

async function набрать(env, q) {
  env.input.value = q;
  await env.input.fire("input");
  await new Promise((r) => setTimeout(r, 320));
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
}
const ссылки = (el) => потомки(el).filter((e) => e.tagName === "A").map((a) => a.getAttribute("href"));
const по_классу = (el, cls) => потомки(el).filter((e) => String(e.className).split(" ").includes(cls));

test("на странице раздела полоска встаёт сверху, свои ссылки страницы на месте", () => {
  const env = окружение();
  const [первый, второй] = env.body.children;
  assert.equal(первый.attrs.id, "kvps-bar");
  assert.equal(первый.attrs.role, "search");
  assert.equal(второй.id, "topbar", "шапка страницы должна остаться следом");
  assert.equal(второй.children[0].href, "/", "ссылка страницы «← Портал» должна остаться");
  assert.equal(ссылки(первый)[0], "/", "в полоске — ссылка на портал");
  assert.ok(env.input, "нет строки поиска");
  assert.equal(env.out.hidden, true, "пустая выдача не показывается");
  // Стили — одним блоком в head, а не атрибутами.
  assert.equal(env.head.children.filter((e) => e.tagName === "STYLE").length, 1);
});

test("на стартовой странице строка рисуется в отведённом месте, полоски нет", () => {
  const env = окружение({ место: true, путь: "/" });
  assert.equal(env.body.children[0].id, "topbar");
  const место = env.body.children[1];
  assert.equal(место.id, "kvps");
  assert.ok(потомки(место).includes(env.input));
  assert.ok(!потомки(env.body).some((e) => e.attrs.id === "kvps-bar"));
  assert.match(потомки(место).map((e) => e.textContent).join(" "), /ИНН, домену или номеру KV-S/);
});

test("выдача по видам: код и бренд рядом, у каждой строки — адрес существующей страницы", async () => {
  const env = окружение();
  await набрать(env, "AB-6205");
  assert.deepEqual(env.запросы, ["/api/portal/search?q=AB-6205"]);
  assert.equal(env.out.hidden, false);
  const группы = по_классу(env.out, "kvps-group");
  // Заголовок группы — имя вида (свой текст узла) и число строк (вложенный).
  assert.deepEqual(группы.map((g) => по_классу(g, "kvps-gh")[0]._text),
    ["Коды", "Поставщики", "Бренды", "Машины", "Узлы"]);
  const [коды] = группы;
  assert.deepEqual(по_классу(коды, "kvps-cols")[0].children.map((c) => c.textContent), ["Код", "Бренд"]);
  const [первый, второй] = по_классу(коды, "kvps-code");
  // Первая колонка — код, вторая — бренд, рядом.
  assert.equal(первый.children[0].getAttribute("href"), "/nomenclature#k=ab6205");
  assert.equal(первый.children[0].textContent, "AB-6205");
  assert.equal(первый.children[1].children[0].getAttribute("href"), "/brands#b=skf");
  assert.equal(первый.children[1].children[0].textContent, "SKF");
  assert.ok(ссылки(первый).includes("/brands#c=ab6205"), "нет перехода к цене кода");
  assert.match(первый.textContent, /сделок 2 · строк КП 3 от 2 пост\./);
  // Бренд без ключа реестра — слово, и ведёт на поиск бренда по слову.
  assert.equal(второй.children[1].children[0].getAttribute("href"),
    "/brands#n=" + encodeURIComponent("Выдуманный литейщик"));
  assert.equal(второй.children[1].children[0].className, "kvps-word");
  const все = ссылки(env.out);
  for (const href of ["/suppliers#e=KV-S-000011-1", "/brands#b=skf", "/library#segment=gtu",
                      "/library#section=component"]) {
    assert.ok(все.includes(href), href);
  }
});

test("строка из базы разметкой не становится", async () => {
  const env = окружение();
  await набрать(env, "AB-6205");
  assert.ok(потомки(env.out).some((e) => e.textContent === "<img src=x onerror=alert(1)>"));
  assert.ok(env.созданные.every((e) => e.innerHTML === ""), "где-то использован innerHTML");
});

test("без права на библиотеку машина и узел — без ссылки, и это сказано", async () => {
  const env = окружение({ ответ: () => [200, { ...ОТВЕТ, library: false }] });
  await набрать(env, "AB-6205");
  const все = ссылки(env.out);
  assert.ok(!все.some((h) => h.startsWith("/library")), все.join(" "));
  assert.match(env.out.textContent, /библиотека закрыта правом/);
});

test("одна буква в базу не ходит; отказ базы и «не успели» — словами", async () => {
  const env = окружение({ ответ: (url) => url.includes("q=AB")
    ? [503, { error: "search_not_installed" }]
    : [200, { q: "QX", rows: [], partial: ["код"], library: false }] });
  await набрать(env, "A");
  assert.deepEqual(env.запросы, []);
  assert.equal(env.out.hidden, true);
  await набрать(env, "AB-6205");
  assert.match(env.out.textContent, /Поиск ещё не установлен в базе/);
  await набрать(env, "QX");
  assert.match(env.out.textContent, /Ничего не нашлось по «QX»/);
  assert.match(env.out.textContent, /Не успели посчитать: коды/);
});

test("Enter открывает первую строку; переход на эту же страницу перечитывает её", async () => {
  const env = окружение({ путь: "/nomenclature" });
  await набрать(env, "AB-6205");
  const первая = потомки(env.out).find((e) => e.tagName === "A");
  let отменено = false;
  // Та же страница, другой «#»: номенклатура читает адрес только при загрузке.
  for (const fn of первая.events.click) fn({ currentTarget: первая, preventDefault() { отменено = true; } });
  assert.equal(отменено, true);
  assert.equal(env.location.hash, "#k=ab6205");
  assert.equal(env.location.reloaded, 1);
  // Enter в строке поиска открывает первую строку выдачи.
  for (const fn of env.input.events.keydown) fn({ key: "Enter", preventDefault() {} });
  assert.equal(env.location.href, "/nomenclature#k=ab6205");

  // Бренды сами переходят по смене «#»: перезагружать их незачем.
  const бренды = окружение({ путь: "/brands" });
  await набрать(бренды, "AB-6205");
  const код = потомки(бренды.out).find((e) => e.tagName === "A" && e.getAttribute("href") === "/brands#c=ab6205");
  let отменено2 = false;
  for (const fn of код.events.click) fn({ currentTarget: код, preventDefault() { отменено2 = true; } });
  assert.equal(отменено2, false);
  assert.equal(бренды.location.reloaded, 0);
});
