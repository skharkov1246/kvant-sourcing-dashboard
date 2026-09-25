// Страница карточек /p (public/portal_entity.html + portal_entity.js) на
// придуманных ответах. Браузера здесь нет — мини-DOM (minidom.mjs).
//
// Что проверяется по существу, а не «не упало»:
//   · заголовок карточки кода — «Код · Бренд», бренд реестра — ссылкой на его
//     карточку, слово без реестра — словом;
//   · в каждой таблице, где есть «Код», следующая колонка — «Бренд»;
//   · оригинал и аналоги — разными таблицами, у аналога — причина; оригинал
//     без названного бренда — с пометкой «оригинал не подтверждён»; бренд
//     позиции спорят только КП — одна таблица «Предложения» без деления;
//   · нечитаемое количество — «не знаем», а не число, и сумма при нём тоже;
//   · условие КП без значения — с причиной: «в КП не указано» и «разбор не
//     дошёл» различимы; над таблицей — ориентир цены по валюте;
//   · компания не из справочника — ссылкой в карточку Битрикса, номер только в
//     адресе, не в тексте; служебных слов («сведена», «засев») на экране нет;
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
  offers: { rows: 3, suppliers: 3, cards: 3, capped: false, brand_judged: true, brand_disputed: false,
    original_n: 102, analog_n: 1, unconfirmed_n: 1,
    original: [
      { company: { id: "KV-S-000011-1", name: "Альфа-Подшипник", src: "bitrix:title", number: null },
        brand: { key: "kelton", name: "Kelton GmbH" }, written: "KL-7", price: 1234.5, currency: "EUR",
        qty: 2, qty_hidden: false, unit: "шт", total: 2469, total_hidden: false, basis: "DDP", basis_src: "строка",
        lead_days: 30, lead_src: "файл", make_days: null, make_src: "нет", pay_terms: "30/70", pay_advance_pct: 30,
        pay_src: "строка", month: "2026-03", month_src: "документ", rfq: "4401", unconfirmed: null },
      { company: { id: "KV-S-000012-2", name: "Бета Уплотнения", src: "написание", number: "KV-S-000012-2" },
        brand: null, written: "KL-7", price: 95, currency: "EUR",
        qty: null, qty_hidden: true, unit: null, total: null, total_hidden: true, basis: null, basis_src: null,
        lead_days: null, month: null, month_src: "нет", unconfirmed: "бренд в КП не назван" }],
    analog: [{ company: null, bx: "91301", unresolved: true, brand: { key: null, name: "Выдуманный литейщик" },
               written: "KL-7A", price: 90, currency: null, qty: null, qty_hidden: false, unit: null, total: null,
               basis: null, lead_days: null, month: "2026-03", month_src: "письмо", why: "поставщик пишет «аналог»" }],
    prices: [{ group: "original", currency: "EUR", rows: 2, companies: 2, min: 95, max: 1234.5,
               last: { price: 1234.5, month: "2026-03", company: { id: "KV-S-000011-1", name: "Альфа-Подшипник" } } },
             { group: "original", currency: "RUB", rows: 1, companies: 1, min: 7, max: 7,
               last: { price: 7, month: "2026-01", company: null, bx: "91301" } }] },
  analogs: [{ code: "an4004", written: "AN-4004", kind: "аналог", brand: { key: null, name: "Выдуманный литейщик" } },
            { code: null, written: "SS316", kind: "аналог", brand: null }],
  analog_of: [{ code: "zc2002", written: "ZC-2002", kind: "замена", brand: { key: "kelton", name: "Kelton GmbH" } }],
  machines: [{ id: "vm400", name: "ВМ-400", kind: "турбина", segment: "gtu", segment_name: "ГТУ выдуманные",
               brand: { key: "kelton", name: "Kelton GmbH" } }],
  units: [{ id: "hot.liner", name: "Жаровая труба", parent: "Горячая часть", crit: "A" }],
  makers: [{ name: "Выдуманный склад", role: "дистрибьютор", country: "Нигдения", makes: "клапаны", verdict: "in_stock",
             in_stock: "yes", stock_qty: "12", lead_time: "5 дней", price: 88, currency: "EUR" },
           { name: "Выдуманный завод", role: "OEM", country: null, makes: null, verdict: null, in_stock: null,
             stock_qty: null, lead_time: null, price: null, currency: null }],
  makers_n: 2,
  write_to: [{ company: { id: "KV-S-000013-5", name: "Гамма Выдуманная", src: "bitrix:title", number: null },
               codes: 2, rows: 3, last_month: "2026-02" }],
  registry: true, partial: ["кому ещё писать"], library: false,
};
// Бренд позиции называют только КП, и они спорят: строки не делятся.
const КОД_СПОР = { ...КОД, key: "pr3003", written: "PR-3003", analog_of: [], makers: [], makers_n: 0, write_to: [],
  brand: { key: "kelton", name: "Kelton GmbH", src: "КП", disputed: true }, partial: [],
  offers: { ...КОД.offers, brand_judged: false, brand_disputed: true, original_n: 2, analog_n: 0, unconfirmed_n: 0,
            analog: [], prices: [] } };
const БРЕНД = {
  key: "kelton", name: "Kelton GmbH", country: "Нигдения", owner: "Выдуманный холдинг", former_names: null,
  spellings: ["Келтон", "Kelton"], demand: { rows: 3, deals: 3, codes: 2, capped: false, registry_rows: 3 },
  codes_demand: [{ code: "qx1001", written: "QX-1001", deals: 2, rows: 2 }],
  codes_offers: [{ code: "pr3003", written: "PR-3003", rows: 2, suppliers: 1, last_month: "2026-02" }],
  offers: { rows: 7, capped: false, suppliers: 2, rows_unresolved: 1, analog_rows: 2 },
  catalog: { parts: 3, list: [{ code: "zc2002", written: "ZC-2002", name: "Седло", kv_no: "KV-000753-4" }] },
  machines: [{ id: "vm400", name: "ВМ-400", kind: "турбина", segment: "gtu", segment_name: "ГТУ выдуманные", parts: 2 }],
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
  brands: [{ brand: { key: "kelton", name: "Kelton GmbH" }, named_codes: 1, asked_codes: 1, rows: 3, last_month: "2026-03" }],
  codes: [{ code: "kl7", written: "KL-7", brand: { key: "kelton", name: "Kelton GmbH" }, brand_src: "назвал поставщик",
            verdict: "оригинал", why: null, price: 100, currency: "EUR", qty: 2, unit: "шт", month: "2026-03", offers: 1 },
          { code: "zc2002", written: "ZC-2002", brand: { key: "skf", name: "SKF" }, brand_src: "назвал поставщик",
            verdict: "аналог", why: "назвал SKF, а спрашивали Kelton GmbH", price: 55, currency: "USD", qty: null,
            unit: null, month: "2026-06", offers: 1 },
          { code: "pr3003", written: "PR-3003", brand: { key: "kelton", name: "Kelton GmbH" }, brand_src: "бренд запроса",
            verdict: null, why: null, price: 10, currency: "USD", qty: null, unit: null, month: "2026-02", offers: 2 }],
  registry: true, partial: [], library: false,
};
const ПОСТАВЩИК_С_ОТЗЫВОМ = { ...ПОСТАВЩИК, id: "KV-S-000012-2", merged_from: null, name: "Бета Уплотнения",
  number: "KV-S-000012-2", bitrix: ["91201", "91401"],
  rfq: { sent: 5, answered: 3, quoted: 2, silent: 1, no_outcome: 1, cards: 6 } };

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
  assert.match(аналоги.textContent, /90 \(валюта не указана\)/);
  // Компания не из справочника — ссылка в Битрикс, номер только в адресе.
  const bx = потомки(аналоги).find((e) => e.tagName === "A");
  assert.equal(bx.getAttribute("href"), "https://kvantpro.bitrix24.ru/crm/company/details/91301/");
  assert.equal(bx.getAttribute("rel"), "noopener noreferrer");
  assert.doesNotMatch(bx.textContent, /[0-9]/);
  assert.match(аналоги.textContent, /нет в справочнике поставщиков/);
  // Цена — с валютой и разрядами; нечитаемое количество — «не знаем», и сумма при нём — тоже.
  assert.match(оригинал.textContent, /1 234,5 EUR/);
  const ячейка = (tr, подпись) => tr.children.find((td) => td.getAttribute("data-l") === подпись);
  const [первая, вторая] = строки_таблицы(оригинал);
  assert.equal(ячейка(вторая, "Кол-во").textContent, "не знаем");
  assert.match(ячейка(вторая, "Сумма").textContent, /^не знаем/);
  assert.doesNotMatch(ячейка(вторая, "Сумма").textContent, /999/);
  assert.match(ячейка(вторая, "Месяц квотации").textContent, /в КП и на карточке даты нет/);
  // Условия КП: значение с источником или причина, почему его нет.
  assert.match(ячейка(первая, "Оплата").textContent, /30\/70аванс 30 %/);
  assert.match(ячейка(первая, "Поставка, дн.").textContent, /30из общих условий КП/);
  assert.equal(ячейка(первая, "Изготовл., дн.").textContent, "в КП не указано");
  assert.equal(ячейка(вторая, "Базис").textContent, "разбор не дошёл");
  // Первоисточник цены — карточка запроса в Битриксе.
  assert.ok(ссылки(первая).includes("https://kvantpro.bitrix24.ru/crm/type/166/details/4401/"));
  // Бренд в КП не назван — оригинал не подтверждён, видимой пометкой.
  assert.match(ячейка(вторая, "Бренд").textContent, /оригинал не подтверждён: бренд в КП не назван/);
  assert.match(env.card.textContent, /Из них 1 — оригинал не подтверждён/);
  // Ориентир цены по валюте и оговорка про валюты; сколько строк показано.
  assert.match(env.card.textContent, /EUR: последняя 1 234,5 \(2026-03, Альфа-Подшипник\)от 95 до 1 234,5 · 2 компании · 2 строки/);
  assert.match(env.card.textContent, /RUB: последняя 7 \(2026-01, компании нет в справочнике поставщиков\)/);
  assert.match(env.card.textContent, /Цены в разных валютах. Пересчёта по курсу здесь нет намеренно/);
  assert.match(env.card.textContent, /Показаны 2 из 102, самые свежие/);
  // Шапка: код сам — аналог к другому коду.
  const hero = потомки(env.card).find((e) => e.tagName === "HEADER");
  assert.match(hero.textContent, /По каталогу аналогов этот код — замена к ZC-2002 · Kelton GmbH/);
  // Кто делает деталь: слово проверки — словами для сорсера.
  const исполнители = таблицы(env.card).find((t) => шапка_таблицы(t).includes("Наличие у продавца"));
  assert.match(исполнители.textContent, /есть на складена складе: есть · 12/);
  assert.match(исполнители.textContent, /не проверяли/);
  assert.match(env.card.textContent, /запись прошлой проверки у продавца, сейчас не перепроверены/);
  const все = ссылки(env.card);
  for (const href of ["/p#supplier=KV-S-000011-1", "/p#supplier=KV-S-000012-2", "/p#code=an4004",
                      "/p#brand=kelton", "/p#supplier=KV-S-000013-5", "/p#code=zc2002",
                      "/nomenclature#k=kl7", "/brands#c=kl7"]) {
    assert.ok(все.includes(href), href);
  }
  // Отвергнутый код — текстом, без ссылки, с видимой пометкой; машина без права на библиотеку — без ссылки.
  assert.ok(!все.some((h) => /ss316/i.test(h)), все.join(" "));
  assert.match(env.card.textContent, /SS316не код детали: марка или стандарт/);
  assert.ok(!все.some((h) => h.startsWith("/library")));
  assert.match(env.card.textContent, /библиотека закрыта правом/);
  // Заказчиков не знаем по устройству базы — сноской, а не плиткой.
  assert.match(env.card.textContent, /заказчиков не знаем/);
  assert.ok(!по_классу(env.card, "total").some((t) => /заказчик/.test(t.textContent)));
  assert.match(env.card.textContent, /Не успели посчитать: кому ещё писать/);
  // Служебных слов на экране нет.
  assert.doesNotMatch(env.card.textContent, /сведен|засев|\(слово\)/);
});

test("бренд позиции спорят только КП: одна таблица без деления, «кому писать» не подбирается", async () => {
  const env = await открыть({ hash: "#code=pr3003", ответ: () => [200, КОД_СПОР] });
  const заголовки = потомки(env.card).filter((e) => e.tagName === "H2").map((e) => e.textContent);
  assert.ok(заголовки.some((h) => /^Предложения2$/.test(h)), заголовки.join(" | "));
  assert.ok(!заголовки.some((h) => /оригинала/.test(h)), заголовки.join(" | "));
  assert.match(env.card.textContent, /Оригинал голосованием не выбирается/);
  assert.match(env.card.textContent, /поставщики называют разные — подбирать по бренду не по чему/);
  assert.doesNotMatch(env.card.textContent, /Давали цену оригинала по бренду/);
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
  assert.match(env.card.textContent, /которых нет в справочнике поставщиков: 1/);
  assert.match(env.card.textContent, /Ещё 2 строки КП — ответы аналогом на спрос по бренду/);
  assert.match(env.card.textContent, /Показаны 1 из 3/);
  // Машина — текстом, ссылка ведёт в раздел и так и подписана.
  const раздел = потомки(env.card).find((e) => e.tagName === "A" && e.getAttribute("href") === "/library#segment=gtu");
  assert.equal(раздел.textContent, "раздел «ГТУ выдуманные» в библиотеке →");
  assert.doesNotMatch(env.card.textContent, /сведен|засев/);
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
  assert.equal(bx.textContent, "карточка ↗");
  const все = ссылки(env.card);
  for (const href of ["/p#code=kl7", "/p#brand=kelton", "/suppliers#e=KV-S-000011-1"]) assert.ok(все.includes(href), href);
  assert.match(env.card.textContent, /Отзывчивость по запросам ещё не посчитана/);
  // Бренды: названное поставщиком и спрошенное нами — разными колонками.
  const бренды = таблицы(env.card).find((t) => шапка_таблицы(t)[0] === "Бренд");
  assert.deepEqual(шапка_таблицы(бренды).slice(1, 3), ["Кодов: бренд назвал сам", "Кодов: бренд не назван — по запросу или каталогу"]);
  // Коды: источник бренда и «оригинал / аналог».
  const коды = таблицы(env.card).find((t) => шапка_таблицы(t).includes("Оригинал или аналог"));
  const [kl7, zc, pr] = строки_таблицы(коды);
  assert.match(kl7.textContent, /Kelton GmbHназвал поставщикоригинал/);
  assert.match(zc.textContent, /аналогназвал SKF, а спрашивали Kelton GmbH/);
  assert.match(pr.textContent, /Kelton GmbHбренд запросане судим/);
});

test("карточка поставщика: отзывчивость с «молчали» и долей, карточки Битрикса без номеров в тексте", async () => {
  const env = await открыть({ hash: "#supplier=KV-S-000012-2", ответ: () => [200, ПОСТАВЩИК_С_ОТЗЫВОМ] });
  const плитки = по_классу(env.card, "total").map((t) => t.textContent);
  for (const т of ["5запросов отправлено", "3 · 60 %ответили", "2дали КП", "1молчали", "1без исхода"]) {
    assert.ok(плитки.includes(т), плитки.join(" | "));
  }
  assert.match(env.card.textContent, /«Без исхода» — ни ответа, ни отказа/);
  const bx = потомки(env.card).filter((e) => e.tagName === "A" && /bitrix24/.test(e.getAttribute("href")));
  assert.deepEqual(bx.map((a) => a.textContent), ["карточка 1 ↗", "карточка 2 ↗"]);
  assert.deepEqual(bx.map((a) => a.getAttribute("href")), ["https://kvantpro.bitrix24.ru/crm/company/details/91201/",
                                                         "https://kvantpro.bitrix24.ru/crm/company/details/91401/"]);
  assert.equal(env.document.title, "Бета Уплотнения · поставщик · КВАНТ");
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
