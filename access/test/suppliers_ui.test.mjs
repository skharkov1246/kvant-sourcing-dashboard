// Карточка поставщика: раздел «На что давал предложения» на придуманном снимке.
// Браузера здесь нет — мини-DOM (minidom.mjs), которого хватает странице.
//
// ЗАЧЕМ. С 24.09.2026 снимок номенклатуры разложен по ключам (library/crossref.py,
// разложить): предложений в строке списка больше нет, и список позиций у
// компании собирается из рёбер e — [номер компании в companies, строк, цена,
// валюта, дата]. Ошибка в этой сборке не роняет страницу, а молча показывает
// «предложений нет», то есть занижает работу компании, — поэтому проверяется
// содержание, а не «не упало».
//
// Прежний снимок (предложения в строке, рёбер нет) тоже обязан читаться: новая
// страница выкатывается раньше, чем публикатор перепишет ключ.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { открыть } from "./minidom.mjs";

const html = fs.readFileSync(new URL("../../public/suppliers.html", import.meta.url), "utf8");

const РЕЕСТР = {
  version: 1, published_at: "2026-09-24T00:00:00Z", totals: { entities: 1 },
  entities: [{ number: "KV-S-000001-8", name: "Учебный завод", sources: ["bitrix"] }],
};
const СБОРКА = "2026-09-24T03:00:00Z";
// Два ключа портала у одной сущности реестра (сведение их объединило) и третья
// компания без сущности — её рёбра в карточку не идут.
const КОМПАНИИ = [
  { co: "101", ent: "KV-S-000001-8", name: "Учебный завод", rows: 3, parts: 2, brands: ["SKF"],
    oem: ["CHINA-BRG"] },
  { co: "105", ent: "KV-S-000001-8", name: "Учебный завод", rows: 1, parts: 1, brands: [] },
  { co: "777", ent: null, name: null, rows: 2, parts: 1, brands: [] },
];
const ЗАГОЛОВОК = { version: 1, published_at: СБОРКА, lists: 2, parts: 32,
  totals: { positions: 3 }, companies: КОМПАНИИ };
const ЧАСТИ = [
  { version: 1, part: 0, published_at: СБОРКА, positions: [
    { k: "6205", n: "6-205", name: "Подшипник учебный", co: 1, offers: 2, b: 3,
      e: [[0, 2, 100, "EUR", "2026-09-01"]] },
    // Две компании: сведённая (105, второй ключ той же сущности) и несведённая
    // 777 — её строки к сущности не прибавляются.
    { k: "sealkit12", n: "SEAL-KIT-12", name: "Комплект уплотнений", co: 2, offers: 3, b: 4,
      e: [[1, 1, 95, "USD", "2026-09-02"], [2, 2, 50]] },
  ] },
  { version: 1, part: 1, published_at: СБОРКА, positions: [
    // Цены нет, дата есть: пустое место посередине ребра хранится null.
    { k: "oring5", n: "O-RING-5", name: "Кольцо", co: 1, offers: 1, b: 3,
      e: [[0, 1, null, null, "2026-09-03"]] },
  ] },
];

async function карточка(маршруты) {
  const запросы = [];
  const { карта } = await открыть(html, {
    hash: "#e=KV-S-000001-8",
    маршруты: (url) => { запросы.push(url); return маршруты(url); },
  });
  // Раздел предложений читает снимок своей цепочкой обещаний — даём ей дойти.
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
  return { карта, запросы };
}

function частями(url, { сборка_части = СБОРКА } = {}) {
  if (url === "/api/suppliers") return РЕЕСТР;
  if (url === "/api/crossref") return ЗАГОЛОВОК;
  const m = url.match(/^\/api\/crossref\/rows\?l=(\d\d)$/);
  if (m) return { ...ЧАСТИ[Number(m[1])], published_at: сборка_части };
  throw new Error("неожиданный адрес " + url);
}

test("список позиций компании собран из рёбер всех частей", async () => {
  const { карта, запросы } = await карточка(частями);
  assert.equal(карта.card.hidden, false, "карточка по прямому адресу не открылась");
  const t = карта.card.textContent;
  // Счётчики — сумма по двум ключам портала одной сущности.
  assert.match(t, /Позиций, на которые давал\s*3/);
  assert.match(t, /Строк в котировках\s*4/);
  // Позиции из обеих частей и обоих ключей портала, по ссылке на номенклатуру.
  const ссылки = карта.card.querySelectorAll("a").map((a) => String(a.href || ""));
  for (const k of ["6205", "sealkit12", "oring5"]) {
    assert.ok(ссылки.includes("/nomenclature#k=" + k), k);
  }
  // 6205: две строки, цена — с кодом валюты.
  assert.match(t, /6-205Подшипник учебный2100 EUR/);
  // sealkit12: одна строка второго ключа; две строки несведённой 777 не прибавлены.
  assert.match(t, /SEAL-KIT-12Комплект уплотнений195 USD/);
  // oring5: цены нет — прочерк, дата на своём месте, а не на месте цены.
  assert.match(t, /O-RING-5Кольцо1—2026-09-03/);
  // Расхождения со счётчиком нет: рёбра посчитаны по всем строкам.
  assert.doesNotMatch(t, /снимок отдаёт не все/);
  // Корзины карточке поставщика не нужны.
  assert.ok(!запросы.some((u) => u.includes("/offers")), "карточка поставщика читает корзины");
  assert.ok(запросы.includes("/api/crossref/rows?l=01"));
});

test("часть другой сборки — раздел говорит, что не прочитался, а не врёт", async () => {
  const { карта } = await карточка((url) => частями(url, { сборка_части: "2026-09-23T03:00:00Z" }));
  const t = карта.card.textContent;
  assert.match(t, /Снимок котировок не прочитался/);
  assert.doesNotMatch(t, /Позиций, на которые давал/);
});

test("прежний единый снимок с предложениями в строке читается как раньше", async () => {
  const прежний = { version: 1, companies: КОМПАНИИ.slice(0, 1), totals: {}, positions: [
    { k: "6205", n: "6-205", name: "Подшипник учебный", co: 1, offers: 1, shown: 1,
      list: [{ c: "101", p: 100, u: "EUR", d: "2026-09-01" }] },
  ] };
  const { карта, запросы } = await карточка((url) => (url === "/api/suppliers" ? РЕЕСТР : прежний));
  assert.match(карта.card.textContent, /6-205Подшипник учебный1100 EUR/);
  assert.ok(!запросы.some((u) => u.includes("/rows")), "частей у прежнего снимка нет");
});
