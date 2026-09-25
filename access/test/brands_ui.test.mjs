// Отрисовка страницы «Бренды и коды» на придуманном снимке. Браузера здесь нет —
// только мини-DOM (minidom.mjs), которого хватает странице.
//
// Что проверяется по существу, а не «не упало»:
//   · плитки сводки берут числа из итогов, а «не определено» видно числом;
//   · облако и список ведут в карточку прямым адресом (#b=, #s=, #c=);
//   · у каждого раздела карточки бренда подписан источник;
//   · «кто давал цену» (адрес по детали) и реестр разведки (родовой адрес) —
//     раздельно;
//   · цена кода никогда не печатается без валюты;
//   · ссылка со словом поставщика (#n=) находит бренд по написанию словаря.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { открыть, потомки } from "./minidom.mjs";

const html = fs.readFileSync(new URL("../../public/brands.html", import.meta.url), "utf8");

const СВОДКА = {
  version: 1, published_at: "2026-09-24T01:00:00Z",
  fields: [
    { id: "models", label: "Машины", src: "lib_models.oem" },
    { id: "units", label: "Узлы и агрегаты", src: "lib_units" },
    { id: "offers", label: "Предложения по кодам бренда", src: "lib_prices, поток «разбор КП»" },
    { id: "registry", label: "Реестр разведки", src: "lib_part_suppliers" },
  ],
  totals: { locale_ok: true, tiles: [
    { id: "all", label: "кодов в базе всего", value: 149637, src: "всё · …" },
    { id: "kp", label: "с ценой из КП", value: 10917, src: "цены КП · …" }] },
  coverage: { undefined: { brands_without_dict_key: 1, brands: 2, parts_without_unit: 6,
                           parts: 10, suppliers_without_name: 1, suppliers: 2 },
              universes: { catalog: { total: 1, fields: [
                { id: "models", label: "Машины", filled: 1, total: 1, pct: 100, status: "закрыто" }] } } },
  brands: [
    { k: "skf", name: "Учебный бренд", dict: true, spellings: ["Skf Gmbh", "SKF"], spellings_n: 2,
      codes: { any: 3, customer: 2, customer_kp: 1, deals: 2 }, sups: 1, priced: 1, asked: 2,
      models: [{ name: "Выдуманная машина", use: "насосная станция", fleet: 2 }], models_n: 1, fleet: 2,
      units: { machine: { units: 1, parts: 10, undefined: 6,
                          list: [{ id: "hot", name: "Горячая часть", crit: "A", parts: 4 }] } },
      parts: { n: 10, unit: 4 },
      registry: { n: 1, list: [{ name: "Разведанная компания", role: "oem", src: ["каталог ЗИП"], parts: 2 }] } },
    { k: "акмеро", name: "Акмеро", dict: false, codes: { any: 1, customer: 1 } },
  ],
  suppliers: [
    { k: "KV-S-000001-1", name: "Альфа-Коготь ООО", from: "Битрикс: карточка компании 101",
      keys: ["101"], codes: 2, rows: 3, cards: 2, cur: ["USD"] },
    { k: "bitrix:777", name: "Компания портала 777", from: "имени нет ни в базе, ни в Битриксе",
      keys: ["777"], codes: 1 },
  ],
  card_brands: [{ id: "501", name: "SKF", k: "skf", codes: 2 }],
};
const СВЯЗИ = { version: 1, brands: ["skf", "акмеро"], suppliers: ["KV-S-000001-1", "bitrix:777"],
  codes: [["ab6205", "AB-6205", 3, [0], [0, 1], 1]] };
const ПАРЫ = { version: 1, brands: ["skf"], suppliers: ["KV-S-000001-1"],
  pairs: [[0, 0, 1, 1, 1, 0, 0, 1, 2, "USD 1", 1]] };
const КОРЗИНА = { version: 1, part: 3, codes: { ab6205: {
  n: "AB-6205", name: "Подшипник выдуманный", asked: true, deals: 2, sups: 2,
  bs: "FAG; SKF", bsk: ["fag", "skf"],
  offers: [
    { s: "KV-S-000001-1", cur: "USD", unit: "шт", min: 10, med: 11, max: 12, rows: 2, drop: 1,
      d1: "2026-09-01", d2: "2026-09-05", br: "SKF", brk: ["skf"], card: ["501"] },
    { s: "bitrix:777", cur: "(не названа)", unit: "шт", min: 9, med: 9, max: 9, rows: 1 },
  ] } } };

function маршруты(url) {
  if (url === "/api/brands") return СВОДКА;
  if (url === "/api/brands/links") return СВЯЗИ;
  if (url === "/api/brands/pairs") return ПАРЫ;
  if (url === "/api/brands/codes?b=03") return КОРЗИНА;
  throw new Error("неожиданный адрес " + url);
}

async function страница(hash = "", сводка = null) {
  return открыть(html, { маршруты: сводка ? () => сводка : маршруты, hash });
}
const ссылки = (el) => потомки(el).filter((e) => e.tagName === "A").map((a) => a.href);

test("сводка: плитки из итогов и «не определено» числом", async () => {
  const { карта } = await страница();
  const плитки = карта.totals.textContent;
  assert.match(плитки, /149\s?637/);
  assert.match(плитки, /с ценой из КП/);
  const оговорка = карта.caveat.textContent;
  assert.equal(карта.caveat.hidden, false);
  assert.match(оговорка, /бренд без ключа словаря — 1 из 2/);
  assert.match(оговорка, /деталь без узла — 6 из 10/);
  assert.equal(карта.status.hidden, true);
});

test("облако и список брендов ведут в карточку прямым адресом", async () => {
  const { карта } = await страница();
  const адреса = ссылки(карта.view);
  assert.ok(адреса.includes("#b=skf"));
  assert.ok(адреса.includes("#b=" + encodeURIComponent("акмеро")));
  // Имя без ключа словаря помечено словами, а не только курсивом.
  assert.match(карта.view.textContent, /имя без ключа/);
});

test("карточка бренда: разделы с источником, адрес по детали отдельно от реестра", async () => {
  const { карта } = await страница("#b=skf");
  const card = карта.card;
  assert.equal(card.hidden, false);
  const т = card.textContent;
  assert.match(т, /Учебный бренд/);
  assert.match(т, /Источник: lib_models\.oem/);
  assert.match(т, /Выдуманная машина/);
  assert.match(т, /узел не определён у 6/);
  // Кто давал цену — по паре, с именем поставщика из сводки.
  assert.match(т, /Кто давал цену по кодам бренда/);
  assert.match(т, /Альфа-Коготь ООО/);
  assert.ok(ссылки(card).includes("#s=KV-S-000001-1"));
  // Реестр разведки — отдельной таблицей и с оговоркой про родовой адрес.
  assert.match(т, /Родовой адрес/);
  assert.match(т, /Разведанная компания/);
  // Коды бренда — ссылками на карточку кода.
  assert.ok(ссылки(card).includes("#c=ab6205"));
});

test("карточка кода: цены по поставщикам, валюта всегда рядом с числом", async () => {
  const { карта } = await страница("#c=ab6205");
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  const т = карта.card.textContent;
  assert.match(т, /AB-6205/);
  assert.match(т, /10 USD/);
  assert.match(т, /12 USD/);
  assert.match(т, /9 \(валюта не названа\)/);
  assert.match(т, /отсеяно 1/);
  // Бренд карточки запроса подписан именем из Битрикса, а не номером.
  assert.match(т, /SKF/);
  assert.ok(ссылки(карта.card).includes("/nomenclature#k=ab6205"));
});

test("карточка поставщика: ссылка в Битрикс и бренды по парам", async () => {
  const { карта } = await страница("#s=KV-S-000001-1");
  const т = карта.card.textContent;
  assert.match(т, /Альфа-Коготь ООО/);
  assert.match(т, /Битрикс: карточка компании 101/);
  assert.ok(ссылки(карта.card).some((a) => /crm\/company\/details\/101\//.test(a)));
  assert.ok(ссылки(карта.card).includes("#b=skf"));
  assert.ok(ссылки(карта.card).includes("/suppliers#e=KV-S-000001-1"));
});

test("слово поставщика находит бренд по написанию словаря", async () => {
  const { карта } = await страница("#n=" + encodeURIComponent("Skf Gmbh"));
  assert.equal(карта.card.hidden, false);
  assert.match(карта.card.textContent, /Учебный бренд/);
});

test("снимка ещё нет — «пока пусто», а не пустой экран", async () => {
  const { карта } = await страница("", { version: 1, brands: [], suppliers: [], totals: {} });
  assert.equal(карта.status.hidden, false);
  assert.match(карта.status.textContent, /Пока пусто/);
});

test("поиск по всему спросу: код с ценой ведёт в карточку, без цены — помечен", async () => {
  const запросы = [];
  const { карта, создано } = await открыть(html, { hash: "", маршруты: (url) => {
    if (url.startsWith("/api/brands/search")) {
      запросы.push(url);
      return { q: "AB-6205", key: "ab6205",
        by_code: [{ code: "ab6205", written: "AB-6205", name: "Подшипник выдуманный", rows: 3, deals: 2 }],
        by_words: [{ code: "zz1", written: "ZZ-1", name: "Подшипник другой", rows: 1, deals: 1 }],
        word_rows: 5000, capped: true };
    }
    return маршруты(url);
  } });
  const вкладка = потомки(карта.tabs).find((e) => e.tagName === "BUTTON" && /Коды с ценой/.test(e.textContent));
  await вкладка.fire("click");
  const поле = создано.find((e) => e.id === "dbq");
  поле.value = "AB-6205";
  const кнопка = создано.find((e) => e.tagName === "BUTTON" && e.textContent === "Искать в базе");
  await кнопка.fire("click");
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(запросы, ["/api/brands/search?q=AB-6205"]);
  const т = карта.view.textContent;
  assert.match(т, /Подшипник выдуманный/);
  assert.match(т, /Подшипник другой/);
  assert.match(т, /посчитаны по первым 5\s?000/);
  assert.doesNotMatch(т, /Строк с этим кодом/, "в предел упёрлись слова, а не код");
  assert.ok(ссылки(карта.view).includes("#c=ab6205"));
  assert.ok(!ссылки(карта.view).includes("#c=zz1"), "код без цены КП не должен вести в пустую карточку");
});

// Предел строк стоит на обоих путях (lib_code_search). Упёрся код — пометка
// у кода; пометка слов со счётом «0 строк» была бы враньём.
async function найти_в_базе(ответ) {
  const { карта, создано } = await открыть(html, { hash: "", маршруты: (url) =>
    (url.startsWith("/api/brands/search") ? ответ : маршруты(url)) });
  const вкладка = потомки(карта.tabs).find((e) => e.tagName === "BUTTON" && /Коды с ценой/.test(e.textContent));
  await вкладка.fire("click");
  создано.find((e) => e.id === "dbq").value = "бн";
  await создано.find((e) => e.tagName === "BUTTON" && e.textContent === "Искать в базе").fire("click");
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  return карта.view.textContent;
}

test("поиск по всему спросу: в предел упёрся код — пометка у кода, не у слов", async () => {
  const т = await найти_в_базе({ q: "бн", key: "бн",
    by_code: [{ code: "бн", written: "б/н", name: "Деталь выдуманная", rows: 5000, deals: 812 }],
    by_words: [], word_rows: 0, capped: true });
  assert.match(т, /Строк с этим кодом не меньше 5\s?000/);
  assert.doesNotMatch(т, /Совпадений больше/);
});

test("поиск по всему спросу: предела не достигли — пометок нет", async () => {
  const т = await найти_в_базе({ q: "бн", key: "бн",
    by_code: [{ code: "бн", written: "б/н", name: "Деталь выдуманная", rows: 40, deals: 12 }],
    by_words: [], word_rows: 0, capped: false });
  assert.match(т, /Деталь выдуманная/);
  assert.doesNotMatch(т, /Строк с этим кодом|Совпадений больше/);
});
