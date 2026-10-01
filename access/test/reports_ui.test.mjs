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
