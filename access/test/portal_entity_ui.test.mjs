// Страница карточек /p (public/portal_entity.html + portal_entity.js) на
// придуманных ответах. Браузера здесь нет — мини-DOM (minidom.mjs).
//
// Что проверяется по существу, а не «не упало»:
//   · заголовок карточки кода — «Код · Бренд», бренд реестра — ссылкой на его
//     карточку, слово без реестра — словом;
//   · в каждой таблице, где есть «Код», следующая колонка — «Бренд»;
//   · оригинал и аналоги — разными таблицами, у аналога — причина;
//   · нечитаемое количество — «не знаем», а не число;
//   · каждый код, бренд и компания — ссылка на свою карточку /p#…, отвергнутый
//     код — без ссылки; прежние разделы — вторыми ссылками;
//   · строка из базы разметкой не становится (innerHTML не используется);
//   · отказы — словами; смена «#» перерисовывает карточку.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { Element, потомки, разобрать } from "./minidom.mjs";

const html = fs.readFileSync(new URL("../../public/portal_entity.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../../public/portal_entity.js", import.meta.url), "utf8");

const КОД = {
  key: "kl7", written: "KL-7", name: "<img src=x onerror=alert(1)>", kv_no: null, catalog: true,
  brand: { key: "kelton", name: "Kelton GmbH", src: "каталог", disputed: true },
  brands: [{ key: "kelton", name: "Kelton GmbH", rows: 5, sources: ["каталог", "КП"] },
           { key: "skf", name: "SKF", rows: 1, sources: ["спецификация"] }],
  demand: { rows: 3, deals: 2, units: 1, qty: null, unit: null, qty_hidden: 1, last_month: "2026-09",
            customers: null, capped: false },
  offers: { rows: 3, suppliers: 3, cards: 3, capped: false, brand_judged: true, original_n: 2, analog_n: 1,
    original: [
      { company: { id: "KV-S-000011-1", name: "Альфа-Подшипник", src: "bitrix:title", number: null },
        brand: { key: "kelton", name: "Kelton GmbH" }, written: "KL-7", price: 1234.5, currency: "EUR",
        qty: 2, qty_hidden: false, unit: "шт", total: 2469, basis: "DDP", lead_days: null,
        month: "2026-03", month_src: "документ", why: null },
      { company: { id: "KV-S-000012-2", name: "Бета Уплотнения", src: "написание", number: "KV-S-000012-2" },
        brand: { key: "kelton", name: "Kelton GmbH" }, written: "KL-7", price: 95, currency: "EUR",
        qty: null, qty_hidden: true, unit: null, total: 999, basis: null, lead_days: null,
        month: null, month_src: "нет", why: null }],
    analog: [{ company: null, brand: { key: null, name: "Выдуманный литейщик" }, written: "KL-7A", price: 90,
               currency: null, qty: null, qty_hidden: false, unit: null, total: null, basis: null,
               lead_days: null, month: "2026-03", month_src: "письмо", why: "поставщик пишет «аналог»" }] },
  analogs: [{ code: "an4004", written: "AN-4004", kind: "аналог", brand: { key: null, name: "Выдуманный литейщик" } },
            { code: null, written: "SS316", kind: "аналог", brand: null }],
  analog_of: [],
  machines: [{ id: "vm400", name: "ВМ-400", kind: "турбина", segment: "gtu", brand: { key: "kelton", name: "Kelton GmbH" } }],
  units: [{ id: "hot.liner", name: "Жаровая труба", parent: "Горячая часть", crit: "A" }],
  write_to: [{ company: { id: "KV-S-000013-5", name: "Гамма Выдуманная", src: "bitrix:title", number: null },
               codes: 2, rows: 3, last_month: "2026-02" }],
  registry: true, partial: ["кому ещё писать"], library: false,
};
const БРЕНД = {
  key: "kelton", name: "Kelton GmbH", country: "Нигдения", owner: "Выдуманный холдинг", former_names: null,
  spellings: ["Келтон", "Kelton"], demand: { rows: 3, deals: 3, codes: 2, capped: false, registry_rows: 3 },
  codes_demand: [{ code: "qx1001", written: "QX-1001", deals: 2, rows: 2 }],
  codes_offers: [{ code: "pr3003", written: "PR-3003", rows: 2, suppliers: 1, last_month: "2026-02" }],
  offers: { rows: 7, capped: false, suppliers: 2, rows_unresolved: 1 },
  catalog: { parts: 3, list: [{ code: "zc2002", written: "ZC-2002", name: "Седло", kv_no: "KV-000753-4" }] },
  machines: [{ id: "vm400", name: "ВМ-400", kind: "турбина", segment: "gtu", parts: 2 }],
  suppliers: [{ company: { id: "KV-S-000012-2", name: "Бета Уплотнения", src: "написание", number: "KV-S-000012-2" },
                codes: 3, rows: 3, last_month: "2026-05" }],
  analogs: [{ code: "kl7", written: "KL-7", alt_code: null, alt_written: "SS316", kind: "аналог", brand: null },
            { code: "kl7", written: "KL-7", alt_code: "skf7", alt_written: "SKF-7", kind: "замена",
              brand: { key: "skf", name: "SKF" } }],
  registry: true, partial: [], library: true,
};
const ПОСТАВЩИК = {
  id: "KV-S-000011-1", merged_from: "KV-S-000013-3", name: null, name_src: null, number: null,
  inn: ["7700000001"], domains: ["alpha-bearings.example"], country: "Нигдения", city: null, status: "active",
  bitrix: ["91101"], rfq: null, quotes: { rows: 3, cards: 2, codes: 2, last_month: "2026-03", capped: false },
  brands: [{ brand: { key: "kelton", name: "Kelton GmbH" }, codes: 2, rows: 3, last_month: "2026-03" }],
  codes: [{ code: "kl7", written: "KL-7", brand: { key: "kelton", name: "Kelton GmbH" }, price: 100,
            currency: "EUR", qty: 2, unit: "шт", month: "2026-03", offers: 1 }],
  registry: true, partial: [], library: false,
};

async function открыть({ hash, ответ }) {
  const { карта } = разобрать(html);
  const созданные = [];
  const запросы = [];
  const слушатели = {};
  const document = {
    title: "",
    getElementById: (id) => карта[id] || null,
    createElement: (tag) => { const e = new Element(tag); созданные.push(e); return e; },
  };
  const location = { hash };
  const context = {
    document, location, encodeURIComponent, decodeURIComponent, isFinite, Math, String,
    window: { addEventListener: (k, fn) => { (слушатели[k] ||= []).push(fn); }, scrollTo() {} },
    fetch: (url) => {
      запросы.push(String(url));
      const [status, value] = ответ(String(url));
      return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  await дождаться();
  return { card: карта.card, document, location, запросы, созданные, слушатели };
}
async function дождаться() {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  for (let i = 0; i < 20; i++) await Promise.resolve();
}
const ссылки = (el) => потомки(el).filter((e) => e.tagName === "A").map((a) => a.getAttribute("href"));
const таблицы = (el) => потомки(el).filter((e) => e.tagName === "TABLE");
const шапка_таблицы = (t) => потомки(t).filter((e) => e.tagName === "TH").map((e) => e.textContent);
const строки_таблицы = (t) => потомки(t).filter((e) => e.tagName === "TR").slice(1);
const по_классу = (el, cls) => потомки(el).filter((e) => String(e.className).split(" ").includes(cls));

function код_рядом_с_брендом(card) {
  for (const t of таблицы(card)) {
    const h = шапка_таблицы(t);
    const i = h.indexOf("Код");
    if (i >= 0) assert.equal(h[i + 1], "Бренд", h.join(" | "));
    // Подпись колонки лежит на каждой ячейке — по ней строка встаёт карточкой на телефоне.
    for (const tr of строки_таблицы(t)) {
      assert.deepEqual(tr.children.map((td) => td.getAttribute("data-l")), h);
    }
  }
}

test("карточка кода: «Код · Бренд», оригинал и аналоги раздельно, ссылки на карточки", async () => {
  const env = await открыть({ hash: "#code=KL-7", ответ: () => [200, КОД] });
  assert.deepEqual(env.запросы, ["/api/portal/code?k=KL-7"]);
  const [h1] = потомки(env.card).filter((e) => e.tagName === "H1");
  assert.equal(h1.textContent, "KL-7·Kelton GmbH");
  assert.equal(h1.children[2].getAttribute("href"), "/p#brand=kelton");
  assert.equal(env.document.title, "KL-7 · Kelton GmbH · КВАНТ");
  assert.match(env.card.textContent, /спорно/);
  assert.match(env.card.textContent, /Источники называют разные бренды: Kelton GmbH — каталог, КП; SKF — спецификация/);
  код_рядом_с_брендом(env.card);
  const [оригинал, аналоги] = таблицы(env.card);
  assert.equal(строки_таблицы(оригинал).length, 2);
  assert.ok(!шапка_таблицы(оригинал).includes("Почему аналог"));
  assert.equal(строки_таблицы(аналоги).length, 1);
  assert.ok(шапка_таблицы(аналоги).includes("Почему аналог"));
  assert.match(аналоги.textContent, /поставщик пишет «аналог»/);
  assert.match(аналоги.textContent, /не сведена с реестром/);
  assert.match(аналоги.textContent, /90 \(валюта не указана\)/);
  // Цена — с валютой и разрядами; нечитаемое количество — «не знаем».
  assert.match(оригинал.textContent, /1 234,5 EUR/);
  const вторая = строки_таблицы(оригинал)[1];
  assert.equal(вторая.children[4].textContent, "не знаем");
  assert.match(вторая.children[7].textContent, /в КП и на карточке даты нет/);
  const все = ссылки(env.card);
  for (const href of ["/p#supplier=KV-S-000011-1", "/p#supplier=KV-S-000012-2", "/p#code=an4004",
                      "/p#brand=kelton", "/p#supplier=KV-S-000013-5",
                      "/nomenclature#k=kl7", "/brands#c=kl7"]) {
    assert.ok(все.includes(href), href);
  }
  // Отвергнутый код — текстом, без ссылки; машина без права на библиотеку — без ссылки.
  assert.ok(!все.some((h) => /ss316/i.test(h)), все.join(" "));
  assert.ok(!все.some((h) => h.startsWith("/library")));
  assert.match(env.card.textContent, /библиотека закрыта правом/);
  assert.match(env.card.textContent, /заказчиков не знаем/);
  assert.match(env.card.textContent, /Не успели посчитать: кому ещё писать/);
});

test("строка из базы разметкой не становится", async () => {
  const env = await открыть({ hash: "#code=kl7", ответ: () => [200, КОД] });
  assert.ok(потомки(env.card).some((e) => e.textContent === "<img src=x onerror=alert(1)>"));
  assert.ok(env.созданные.every((e) => e.innerHTML === ""), "где-то использован innerHTML");
});

test("карточка бренда: коды рядом с брендом, машины, поставщики, аналоги других брендов", async () => {
  const env = await открыть({ hash: "#brand=kelton", ответ: () => [200, БРЕНД] });
  assert.deepEqual(env.запросы, ["/api/portal/brand?b=kelton"]);
  assert.equal(потомки(env.card).find((e) => e.tagName === "H1").textContent, "Kelton GmbH");
  код_рядом_с_брендом(env.card);
  const все = ссылки(env.card);
  for (const href of ["/p#code=qx1001", "/p#code=pr3003", "/p#code=zc2002", "/p#supplier=KV-S-000012-2",
                      "/p#brand=skf", "/p#code=skf7", "/library#segment=gtu", "/brands#b=kelton"]) {
    assert.ok(все.includes(href), href);
  }
  assert.ok(!все.some((h) => /ss316/i.test(h)));
  assert.match(env.card.textContent, /не меньше/);
  assert.match(env.card.textContent, /не сведённых с реестром: 1/);
  assert.match(env.card.textContent, /Показаны 1 из 3/);
});

test("карточка поставщика: без имени — «имя не известно», Битрикс и прежний раздел", async () => {
  const env = await открыть({ hash: "#supplier=KV-S-000011-1", ответ: () => [200, ПОСТАВЩИК] });
  assert.deepEqual(env.запросы, ["/api/portal/supplier?s=KV-S-000011-1"]);
  assert.equal(потомки(env.card).find((e) => e.tagName === "H1").textContent, "Имя не известно");
  assert.match(env.card.textContent, /Номер KV-S-000013-3 слит в эту компанию/);
  assert.match(env.card.textContent, /номер не выдан/);
  код_рядом_с_брендом(env.card);
  const bx = потомки(env.card).find((e) => e.tagName === "A" && /bitrix24/.test(e.getAttribute("href")));
  assert.equal(bx.getAttribute("href"), "https://kvantpro.bitrix24.ru/crm/company/details/91101/");
  assert.equal(bx.getAttribute("rel"), "noopener noreferrer");
  const все = ссылки(env.card);
  for (const href of ["/p#code=kl7", "/p#brand=kelton", "/suppliers#e=KV-S-000011-1"]) assert.ok(все.includes(href), href);
  assert.match(env.card.textContent, /Отзывчивость по запросам ещё не посчитана/);
});

test("отказы — словами; смена «#» перерисовывает карточку", async () => {
  const env = await открыть({ hash: "#code=SS316", ответ: (url) => url.includes("SS316")
    ? [404, { error: "not_a_code", key: "ss316" }]
    : url.includes("brand") ? [503, { error: "brands_not_installed" }]
    : url.includes("supplier") ? [403, { error: "forbidden" }] : [200, КОД] });
  assert.match(env.card.textContent, /Это не код детали/);
  for (const [hash, текст] of [["#brand=kelton", /Реестр брендов не установлен/],
                               ["#supplier=KV-S-000011-1", /Нет доступа/],
                               ["#code=kl7", /KL-7·Kelton GmbH/]]) {
    env.location.hash = hash;
    for (const fn of env.слушатели.hashchange) fn();
    await дождаться();
    assert.match(env.card.textContent, текст, hash);
  }
  assert.equal(env.запросы.length, 4);
});

test("без адреса после «#» страница не ходит в базу и подсказывает поиск", async () => {
  const env = await открыть({ hash: "", ответ: () => [200, КОД] });
  assert.deepEqual(env.запросы, []);
  assert.match(env.card.textContent, /строки поиска сверху/);
});

test("страница подключает общую строку поиска и свой скрипт, своя навигация на месте", () => {
  assert.ok(html.includes('<script src="/portal_search.js" defer></script>'));
  assert.ok(html.includes('<script src="/portal_entity.js" defer></script>'));
  for (const href of ['href="/"', 'href="/nomenclature"', 'href="/brands"', 'href="/suppliers"']) {
    assert.ok(html.includes(href), href);
  }
  assert.doesNotMatch(source, /innerHTML/);
});
