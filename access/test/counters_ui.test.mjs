// Отрисовка страницы счётчиков на придуманных точках. Браузера здесь нет — мини-DOM.
//
// ЗАЧЕМ. Страница рисуется скриптом целиком: ошибка в нём даёт пустой экран, а не
// кривую вёрстку. Правило CLAUDE.md про smoke действует и здесь.
//
// Что проверяется по существу, а не «не упало»:
//   · берётся ПОСЛЕДНЯЯ точка, а не первая и не случайная;
//   · дельта к прошлому замеру считается в верную сторону: рост кодов с ценой —
//     хорошо, рост кодов без цены — плохо, и путать их нельзя;
//   · оговорка к точке видна (без неё «цифра упала» читается как провал);
//   · доли считаются от кодов, а не от строк;
//   · одна точка — график не рисуется, но и не врёт линией из ниоткуда;
//   · пустой снимок и отказ по праву объясняются словами.
import fs from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";
import { открыть } from "./minidom.mjs";

const html = fs.readFileSync(new URL("../../public/counters.html", import.meta.url), "utf8");

// ВЫДУМАННЫЕ ТОЧКИ (CLAUDE.md, правило 18). Числа выбраны так, чтобы доли
// считались в уме: 100 → 120 кодов, 10 → 18 с ценой, 88 → 100 без цены.
const ТОЧКА_1 = { run: "111", at: "2026-09-21T01:00:00Z",
  nums: { asked: 100, with_kp: 10, other_feed: 2, no_price: 88, rows_asked: 400,
          rows_with: 40, rows_without: 340, price_codes: 11, price_rows: 20,
          price_asked: 10, price_in_catalog: 3, catalog: 50, catalog_priced: 3,
          catalog_asked: 12, plausible: 95, no_digit: 2, shorter_than_four: 1,
          longer_than_25: 2 } };
const ТОЧКА_2 = { run: "222", at: "2026-09-22T01:00:00Z", note: "замер во время переразбора",
  nums: { asked: 120, with_kp: 18, other_feed: 2, no_price: 100, rows_asked: 430,
          rows_with: 60, rows_without: 330, price_codes: 19, price_rows: 31,
          price_asked: 18, price_in_catalog: 4, catalog: 50, catalog_priced: 4,
          catalog_asked: 14, plausible: 112, no_digit: 3, shorter_than_four: 2,
          longer_than_25: 3 } };

const снимок = (точки) => ({ version: 1, metrics: { "коды_и_цены": точки } });

test("страница показывает последнюю точку, а не первую", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1, ТОЧКА_2]) });
  assert.equal(карта["загрузка"].hidden, true, "плашка загрузки осталась на экране");
  assert.equal(карта["тело"].hidden, false);
  assert.equal(карта["ч-всего"].textContent, "120");
  assert.equal(карта["ч-есть"].textContent, "18");
  assert.equal(карта["ч-нет"].textContent, "100");
  assert.equal(карта["прогон"].textContent, "222");
});

test("точки в перепутанном порядке всё равно дают последнюю по дате", async () => {
  // Порядок задаёт запрос публикатора, но страница на него не полагается:
  // перепутанный порядок нарисовал бы пилу на графике и неверную дельту.
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_2, ТОЧКА_1]) });
  assert.equal(карта["ч-всего"].textContent, "120");
  assert.equal(карта["прогон"].textContent, "222");
});

test("дельта считается в верную сторону по каждому счётчику", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1, ТОЧКА_2]) });
  // Кодов с ценой стало больше — это хорошо.
  assert.match(карта["dt-есть"].textContent, /\+8 к прошлому замеру/);
  assert.match(карта["dt-есть"].className, /\bup\b/);
  // Кодов БЕЗ цены стало больше — это плохо, хотя число тоже выросло.
  assert.match(карта["dt-нет"].textContent, /\+12 к прошлому замеру/);
  assert.match(карта["dt-нет"].className, /\bdown\b/,
    "рост кодов без цены покрашен как улучшение");
});

test("доли считаются от кодов, а не от строк", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_2]) });
  // 18 из 120 = 15,0 %. Если бы делили на строки спроса (430), вышло бы 4,2 %.
  assert.match(карта["д-есть"].textContent, /15,0 %/);
  // 100 из 120 = 83,3 %.
  assert.match(карта["д-нет"].textContent, /83,3 %/);
  assert.match(карта["л-чужой"].textContent, /2 · 1,7 %/);
});

test("оговорка к точке видна", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1, ТОЧКА_2]) });
  assert.equal(карта["оговорка"].hidden, false);
  assert.match(карта["оговорка-текст"].textContent, /во время переразбора/);
});

test("без оговорки плашка не появляется", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1]) });
  assert.equal(карта["оговорка"].hidden, true);
});

test("одна точка — график не рисуется и говорит почему", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1]) });
  assert.match(карта["график"].textContent, /Точка пока одна/);
  assert.equal(карта["график"].innerHTML, "", "нарисован график по одной точке");
  assert.equal(карта["dt-есть"].textContent, "", "дельта посчитана без второй точки");
});

test("две точки — график рисуется двумя линиями", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1, ТОЧКА_2]) });
  const svg = карта["график"].innerHTML;
  assert.match(svg, /<svg/);
  // Обе линии на месте: спрос и цена. Одна линия здесь означала бы, что рост
  // покрытия не с чем сравнить.
  assert.equal((svg.match(/<path /g) || []).length, 2);
  assert.match(svg, /09-21/);
  assert.match(svg, /09-22/);
});

test("история — строка на точку, свежая сверху", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_1, ТОЧКА_2]) });
  const строки = карта["история"].tBodies[0].children;
  assert.equal(строки.length, 2);
  assert.match(строки[0].textContent, /2026-09-22/);
  assert.match(строки[1].textContent, /2026-09-21/);
});

test("разрезы не подменяют знаменатель", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_2]) });
  const строки = карта["разрезы"].tBodies[0].children.map((tr) => tr.textContent);
  assert.equal(строки.length, 4);
  // «Коды, по которым цена есть» считаются от 19, а не от 120.
  assert.ok(строки.some((s) => s.includes("19") && s.includes("94,7 %")),
    "доля спрошенных среди кодов с ценой посчитана не от кодов с ценой: " + строки.join(" | "));
});

test("предложения названы числами этого замера, а не общими словами", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_2]) });
  const дела = карта["дела"].children.map((li) => li.textContent);
  assert.equal(дела.length, 5);
  // Первое предложение обязано нести число кодов без цены: без него это лозунг.
  assert.match(дела[0], /100 кодам без цены/);
  assert.match(дела[0], /83,3 % спроса/);
  // Кодов с ценой вне каталога: 19 − 4 = 15.
  assert.match(дела[2], /15 кодов/);
  // Неправдоподобных: 120 − 112 = 8.
  assert.match(дела[3], /8 кодов/);
});

test("оценка правдоподобия названа оценкой", async () => {
  const { карта } = await открыть(html, { снимок: снимок([ТОЧКА_2]) });
  const t = карта["качество"].textContent;
  assert.match(t, /ОЦЕНКА, а не факт/);
  assert.match(t, /112 из 120/);
  assert.match(t, /Ни одна строка по этой оценке не отбрасывается/);
});

test("пустой снимок — это «пока пусто», а не поломка", async () => {
  const { карта } = await открыть(html, { снимок: { version: 1, metrics: {} } });
  assert.equal(карта["ошибка"].hidden, false);
  assert.match(карта["ошибка-что"].textContent, /Пока пусто/);
  assert.equal(карта["тело"].hidden, true);
});

test("отказ по праву объясняется, а не показывает белый экран", async () => {
  const { карта } = await открыть(html, { ответ:
    new Response("{}", { status: 403, headers: { "Content-Type": "application/json" } }) });
  assert.equal(карта["ошибка"].hidden, false);
  assert.match(карта["ошибка-как"].textContent, /нет права suppliers/);
  assert.match(карта["ошибка-как"].textContent, /Доступы/);
});

test("недоступный снимок отличим от пустого", async () => {
  const { карта } = await открыть(html, { ответ:
    new Response("{}", { status: 503, headers: { "Content-Type": "application/json" } }) });
  assert.match(карта["ошибка-что"].textContent, /Не открылось/);
  assert.match(карта["ошибка-как"].textContent, /503/);
});
