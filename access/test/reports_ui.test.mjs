// Движок конструктора отчётов — без браузера. Корпус придуман (CLAUDE.md, правило 18).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const code = readFileSync(new URL("../../public/reports.js", import.meta.url), "utf8");
const box = {};
vm.runInNewContext(code, { globalThis: box, window: undefined, Map, Set, JSON, Math, Number, String, Array, Object, Promise, isNaN, encodeURIComponent, decodeURIComponent });
const R = box.KVR;

const D = {
  suppliers: { entities: [
    { number: "KV-S-1", name: "Альфа", inn: "1", rfq: { sent: 5, answered: 3, quoted: 2 } },
    { number: "KV-S-2", name: "Бета", inn: null, rfq: { sent: 1 } },
    { number: "KV-S-3", name: "Гамма", inn: "3", wait_inn: true },
  ] },
  crossref: { lists: 1, companies: [{ name: "Альфа", rows: 4 }, { name: "Бета", rows: 1 }],
    positions: [
      { k: "vyd1", n: "VYD-1", bn: "Vydumka", offers: 3, co: 2, demand: { deals: 4 }, e: [[0, 2, 10.5, "USD"], [1, 1, 12, "USD"]] },
      { k: "vyd2", n: "VYD-2", bn: "Vydumka", offers: 1, co: 1, e: [[0, 1, 7, "EUR", "2026-09"]] },
    ] },
  brands: { brands: [{ k: "vydumka", name: "Vydumka", codes: { any: 2 }, priced: 2 }],
    suppliers: [{ k: "alfa", name: "Альфа" }] },
  pairs: { brands: ["vydumka"], suppliers: ["alfa"], pair_fields: ["brand", "supplier", "codes", "priced"], pairs: [[0, 0, 2, 2]] },
};

test("наборы разбираются в плоские строки без выдуманных значений", () => {
  const s = R.НАБОРЫ.suppliers.разобрать(D);
  assert.equal(s.length, 3);
  assert.equal(s[0].sent, 5);
  assert.equal(s[2].sent, undefined, "нет запросов — пусто, а не ноль");
  assert.equal(s[2].wait_inn, "да");
  const o = R.НАБОРЫ.offers.разобрать(D);
  assert.equal(o.length, 3);
  assert.deepEqual([o[0].company, o[0].price, o[2].date], ["Альфа", 10.5, "2026-09"]);
  const p = R.НАБОРЫ.pairs.разобрать(D);
  assert.deepEqual([p[0].brand, p[0].supplier, p[0].priced], ["Vydumka", "Альфа", 2]);
});

test("условия, поиск, группировка и сортировка", () => {
  const s = R.НАБОРЫ.suppliers.разобрать(D);
  assert.deepEqual(R.фильтровать(s, [{ col: "inn", op: "empty" }]).map((r) => r.name), ["Бета"]);
  assert.deepEqual(R.фильтровать(s, [{ col: "sent", op: "gt", val: "1" }]).map((r) => r.name), ["Альфа"]);
  assert.deepEqual(R.фильтровать(s, [], "гам").map((r) => r.name), ["Гамма"]);
  const o = R.НАБОРЫ.offers.разобрать(D);
  const g = R.сортировать(R.сгруппировать(o, "company", [{ fn: "distinct", col: "n" }, { fn: "sum", col: "rows" }]), "__count", "desc");
  assert.equal(g[0].company, "Альфа");
  assert.equal(g[0].__count, 2);
  assert.equal(g[0]["distinct:n"], 2);
  assert.equal(g[0]["sum:rows"], 3);
  // пустые значения уходят в конец при любом направлении
  assert.deepEqual(R.сортировать(s, "sent", "asc").map((r) => r.name), ["Бета", "Альфа", "Гамма"]);
});

test("CSV для Excel: BOM, точка с запятой, кавычки, запятая в дробях", () => {
  const csv = R.вCSV([{ a: "x;y", b: 1.5 }, { a: 'q"', b: null }], [{ id: "a", label: "Поле" }, { id: "b", label: "Число" }]);
  assert.ok(csv.startsWith("﻿Поле;Число\r\n"));
  assert.match(csv, /"x;y";1,5/);
  assert.match(csv, /"q""";$/m);
});

test("готовые отчёты и ссылка на отчёт", () => {
  const s = R.изАдреса("#preset=offers_by_company");
  assert.equal(s.ds, "offers");
  assert.equal(s.group, "company");
  const state = { ds: "positions", filters: [{ col: "offers", op: "ge", val: "2" }], sort: "deals", dir: "desc" };
  assert.deepEqual(R.изАдреса(R.вАдрес(state)), state);
  assert.equal(R.изАдреса("#r=%7Bbroken"), null);
  assert.equal(R.изАдреса('#r=' + encodeURIComponent('{"ds":"secret"}')), null, "неизвестный набор не принимается");
  for (const k of Object.keys(R.ГОТОВЫЕ)) assert.ok(R.НАБОРЫ[R.ГОТОВЫЕ[k].ds], k);
  const res = R.построить("positions", R.НАБОРЫ.positions.разобрать(D), state);
  assert.deepEqual(res.rows.map((r) => r.n), ["VYD-1"]);
});

// Сделки, запросы и заказы поставщикам (deals:v1, 02.10.2026).
const сделки = (money) => ({
  since: "2025-01-01",
  companies: { "500": "Заказчик-Учебный", "700": "Альфа", "702": "Бета" },
  stages: { deal: { EXEC: "Исполнение" }, deal_category: { "0": "Реализация" }, rfq: {}, order: { "DT172_26:NEW": "Новый" } },
  deals: [{ id: "10", title: "Сделка А", customer: "500", category: "0", stage: "EXEC", outcome: "P", created: "2025-03-01" },
    { id: "11", title: "Сделка Б", customer: "500", category: "0", stage: "EXEC", outcome: "P", created: "2025-04-01" }],
  rfq: [{ id: "1", deal: "10", supplier: "700", outcome: "S", quote: true, created: "2025-03-02" },
    { id: "2", deal: "10", supplier: "702", outcome: "F", quote: true },
    { id: "3", deal: "11", supplier: "702", outcome: "P", quote: true }],
  orders: [{ id: "50", title: "PO-1", deal: "10", supplier: "700", stage: "DT172_26:NEW", outcome: "P", created: "2025-05-01" },
    { id: "51", title: "PO-2", deal: "10", supplier: "702", outcome: "F", created: "2025-06-01" }],
  lines: [{ order: "50", name: "Подшипник учебный", qty: 2, unit: "шт" }],
  money,
});
const ДЕНЬГИ = { base: "EUR", rates: { USD: 0.5 }, deals: { "10": [1000, "EUR", 400, 600, "EUR"] },
  orders: { "50": [800, "USD"], "51": [100, "EUR"] }, lines: [[100, 120, 20, "USD"]] };

test("сделки и заказы: связи, исходы, пересчёт в базовую валюту", () => {
  const d = { deals: сделки(ДЕНЬГИ) };
  const дл = R.НАБОРЫ.deals.разобрать(d);
  assert.deepEqual([дл[0].customer, дл[0].category, дл[0].stage], ["Заказчик-Учебный", "Реализация", "Исполнение"]);
  assert.deepEqual([дл[0].rfq, дл[0].rfq_quote, дл[0].asked, дл[0].orders, дл[0].ordered], [2, 2, 2, 2, 2]);
  assert.equal(дл[0].buy_eur, 500, "800 USD × 0,5 + 100 EUR");
  assert.equal(дл[1].orders, 0);
  const зап = R.НАБОРЫ.rfq.разобрать(d);
  assert.equal(зап[0].ordered, "да");
  assert.equal(зап[1].ordered, "", "заказ проигран — не заказ");
  const зак = R.НАБОРЫ.orders.разобрать(d);
  assert.deepEqual([зак[0].supplier, зак[0].stage, зак[0].sum_base, зак[0].lines], ["Альфа", "Новый", 400, 1]);
  const стр = R.НАБОРЫ.order_lines.разобрать(d);
  assert.deepEqual([стр[0].price, стр[0].price_base, стр[0].line_base, стр[0].supplier], [100, 50, 100, "Альфа"]);
  const в = R.НАБОРЫ.supplier_funnel.разобрать(d);
  const бета = в.find((r) => r.supplier === "Бета");
  assert.deepEqual([бета.rfq, бета.orders, бета.orders_live, бета.won_of_asked, бета.buy_base], [2, 1, 0, 1, 0]);
  const альфа = в.find((r) => r.supplier === "Альфа");
  assert.deepEqual([альфа.buy_base, альфа.last_order], [400, "2025-05-01"]);
});

test("без права suppliers_fin деньги — «закрыто», а не ноль и не пусто", () => {
  const d = { deals: сделки({ "закрыто": "suppliers_fin" }) };
  const дл = R.НАБОРЫ.deals.разобрать(d);
  assert.equal(дл[0].sale, "закрыто");
  assert.equal(дл[0].buy_eur, "закрыто");
  assert.equal(R.НАБОРЫ.orders.разобрать(d)[0].sum_base, "закрыто");
  assert.equal(R.НАБОРЫ.order_lines.разобрать(d)[0].price, "закрыто");
  assert.equal(R.НАБОРЫ.order_lines.разобрать(d)[0].qty, 2, "количество не деньги — видно");
});

test("неизвестный курс — нет пересчёта, а не «1 к 1»", () => {
  const d = { deals: сделки({ ...ДЕНЬГИ, rates: {} }) };
  assert.equal(R.НАБОРЫ.orders.разобрать(d)[0].sum_base, null);
});

test("готовые отчёты по заказам строятся; максимум по датам", () => {
  const d = { deals: сделки(ДЕНЬГИ) };
  const st = R.изАдреса("#preset=orders_by_supplier");
  const res = R.построить(st.ds, R.НАБОРЫ[st.ds].разобрать(d), st);
  assert.equal(res.rows.length, 1, "проигранный заказ отсеян");
  assert.equal(res.rows[0]["sum:sum_base"], 400);
  assert.equal(res.rows[0]["max:created"], "2025-05-01");
  for (const k of ["funnel", "purchase_history", "deals_no_order"]) {
    const s = R.изАдреса("#preset=" + k);
    assert.ok(s && R.НАБОРЫ[s.ds], k);
    R.построить(s.ds, R.НАБОРЫ[s.ds].разобрать(d), s);
  }
  const нет = R.изАдреса("#preset=deals_no_order");
  const r2 = R.построить(нет.ds, R.НАБОРЫ[нет.ds].разобрать(d), нет);
  assert.deepEqual(r2.rows.map((r) => r.id), ["11"]);
});

test("группа по закрытым деньгам — «закрыто», а не пусто", () => {
  const d = { deals: сделки({ "закрыто": "suppliers_fin" }) };
  const st = R.изАдреса("#preset=orders_by_supplier");
  const res = R.построить(st.ds, R.НАБОРЫ[st.ds].разобрать(d), st);
  assert.equal(res.rows[0]["sum:sum_base"], "закрыто");
});
