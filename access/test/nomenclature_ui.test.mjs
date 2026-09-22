// Отрисовка карточки товара на придуманном снимке. Браузера здесь нет — только
// минимальный DOM, которого хватает странице.
//
// ЗАЧЕМ. Страница целиком рисуется скриптом: ошибка в нём даёт не кривую
// вёрстку, а пустой экран, и увидит его владелец. Правило CLAUDE.md про smoke
// («вкладка без данных — непроверенная вкладка») действует и здесь.
//
// Что проверяется по существу, а не «не упало»:
//   · четыре утверждения об изготовителе видны РАЗДЕЛЬНО и подписаны источником;
//   · цена никогда не печатается без кода валюты;
//   · доля позиций без выбора стоит наверху, а не в сноске;
//   · компания, не сведённая с реестром, ведёт прямой ссылкой в Битрикс;
//   · закрытое правом поле показывается как «нет доступа», а не как пустота.
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import assert from "node:assert/strict";

const html = fs.readFileSync(new URL("../../public/nomenclature.html", import.meta.url), "utf8");
const source = html.split("<script>")[1].split("</script>")[0];

const разметка = html.split("<script>")[0];
const идентификаторы = [];
for (const m of разметка.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
  const id = m[2].match(/\bid="([^"]+)"/);
  if (!id) continue;
  идентификаторы.push({ id: id[1], tag: m[1], hidden: /\shidden(?:\s|>|$)/.test(m[2]) });
}

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.attrs = {};
    this.events = {};
    this.hidden = false;
    this.value = "";
    this._text = "";
    this.className = "";
  }
  set textContent(v) { this._text = v == null ? "" : String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  appendChild(node) {
    // Настоящий DOM разворачивает DocumentFragment при вставке и опустошает его.
    // Без этого список из трёх строк выглядел бы одним узлом.
    if (node instanceof Fragment) { this.children.push(...node.children); node.children = []; return node; }
    this.children.push(node);
    return node;
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(k, fn) { (this.events[k] ||= []).push(fn); }
  async fire(k) { for (const fn of this.events[k] || []) await fn({ preventDefault() {} }); }
  querySelectorAll(sel) {
    const теги = sel.split(",").map((t) => t.trim().toUpperCase());
    return потомки(this).filter((e) => теги.includes(e.tagName));
  }
  focus() {}
  scrollIntoView() {}
}
class Fragment extends Element {
  constructor() { super("#fragment"); }
}
function потомки(root) { return [root, ...root.children.flatMap(потомки)]; }

const СНИМОК = {
  version: 1,
  totals: { positions: 3, with_choice: 1, comparable: 1, in_catalog: 1,
            companies: 3, companies_resolved: 1, no_choice_with_demand: 1 },
  positions: [
    { k: "6205", n: "6-205", name: "Подшипник учебный", co: 2, offers: 3, shown: 3,
      cmp: true, cat: true, oem_file: ["CHINA-BRG"], oem_cat: "SKF", brands: ["SKF"],
      kv: "KV-000753-4",
      // Спрос двумя сделками в РАЗНЫХ единицах: сумма по такой позиции не даётся.
      demand: { deals: 2, rows: 3, qty: null, units: 2 },
      alts: [{ pn: "180205", kind: "номер изготовителя", maker: "ГПЗ" },
             { pn: "6205-2RS", kind: "аналог", maker: "FAG" }],
      models: ["SGT-400", "Taurus 70"],
      makers: [{ name: "Учебный завод", role: "OEM", country: "Швеция",
                 makes: "подшипники", verdict: null }],
      // Форма как у публикатора: имени компании в предложении НЕТ, пустые
      // ключи отсутствуют вовсе (а не лежат со значением null).
      list: [
        { co: "101", ent: "KV-S-000001-8", price: 100, cur: "EUR", qty: 4,
          unit: "шт", basis: "EXW", lead: 30, brands: ["SKF"],
          oem: "CHINA-BRG", date: "2026-09-01" },
        { co: "777", price: 120, cur: "EUR", date: "2026-09-02" },
        { co: "101", ent: "KV-S-000001-8",
          price: { "закрыто": "suppliers_fin" }, cur: "EUR", date: "2026-09-03" },
      ] },
    { k: "sealkit12", n: "SEAL-KIT-12", name: "Комплект уплотнений", co: 1, offers: 1,
      shown: 1, cmp: false, cat: false, oem_file: [], oem_cat: null, brands: [],
      // Спрос одной сделкой в одной единице: сумма законна. Выбора нет — это и
      // есть строка списка работы.
      demand: { deals: 1, rows: 2, qty: 10, units: 1 },
      alts: [], models: [], makers: [],
      list: [{ co: "102", price: 50, date: "2026-09-01" }] },
    // Позиция БЕЗ спроса: в список работы не идёт, и «не спрашивали» — не ноль.
    { k: "oring5", n: "O-RING-5", name: "Кольцо", co: 1, offers: 1, shown: 1,
      cmp: false, cat: false, oem_file: [], oem_cat: null, brands: [], alts: [],
      models: [], makers: [], list: [] },
  ],
  // Имена компаний лежат здесь один раз — страница берёт их отсюда по ключу.
  companies: [
    { co: "101", ent: "KV-S-000001-8", name: "Учебный завод", rows: 2, parts: 1,
      brands: ["SKF"] },
    { co: "102", ent: null, name: null, rows: 1, parts: 1, brands: [] },
  ],
  rights: ["suppliers"],
};

async function открыть_страницу(снимок = СНИМОК, ответ = null) {
  const карта = {};
  for (const о of идентификаторы) {
    const el = new Element(о.tag);
    el.id = о.id;
    el.hidden = о.hidden;
    карта[о.id] = el;
  }
  const создано = [];
  const doc = {
    body: new Element("body"),
    getElementById: (id) => карта[id] || null,
    createElement: (tag) => { const e = new Element(tag); создано.push(e); return e; },
    createDocumentFragment: () => new Fragment(),
    createTextNode: (t) => Object.assign(new Element("#text"), { textContent: t }),
  };
  const обещания = [];
  const context = {
    document: doc, console, URL, encodeURIComponent,
    fetch: (url) => {
      const p = Promise.resolve(ответ || new Response(JSON.stringify(снимок),
        { status: 200, headers: { "Content-Type": "application/json" } }));
      обещания.push(p);
      return p;
    },
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  // Скрипт читает снимок через fetch: даём микрозадачам дойти до отрисовки.
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
  return { карта, создано };
}

test("страница рисует итоги и список", async () => {
  const { карта } = await открыть_страницу();
  assert.equal(карта.status.hidden, true, "плашка загрузки осталась на экране");
  assert.equal(карта.list.hidden, false);
  assert.match(карта.totals.textContent, /позиций с предложением/);
  assert.match(карта.totals.textContent, /с выбором: 2\+ компании/);
  // Три позиции — три строки.
  assert.equal(карта.rows.children.length, 3);
  assert.match(карта.count.textContent, /3 из 3/);
});

test("вес позиции показан, и количество не суммируется через единицы", async () => {
  const { карта } = await открыть_страницу();
  const строки = карта.rows.children;
  // 6205: две сделки, две единицы — сумма не даётся, и об этом сказано словами.
  const первая = строки[0].textContent;
  assert.match(первая, /2 сделки/);
  assert.match(первая, /единиц 2, сумма не дана/);
  // sealkit12: одна сделка, одна единица — количество печатается.
  const вторая = строки[1].textContent;
  assert.match(вторая, /1 сделка/);
  assert.match(вторая, /· 10/);
  // oring5: спроса нет вовсе.
  assert.match(строки[2].textContent, /Не спрашивали/);
  // Итог наверху называет размер списка работы.
  assert.match(карта.totals.textContent, /спрашивали, а выбора нет/);
});

test("отбор «спрашивали, а выбора нет» — это список работы", async () => {
  const { карта } = await открыть_страницу();
  const кнопки = карта.tabs.querySelectorAll("button");
  const нужная = кнопки.find((b) => b.textContent.includes("Спрашивали, а выбора нет"));
  assert.ok(нужная, "отбора «спрашивали, а выбора нет» нет");
  // В корпусе такая одна: sealkit12. У 6205 выбор из двух компаний, у oring5
  // нет спроса.
  assert.match(нужная.textContent, /· 1$/);
  await нужная.fire("click");
  assert.equal(карта.rows.children.length, 1);
  assert.match(карта.rows.children[0].textContent, /SEAL-KIT-12/);
});

test("доля позиций без выбора стоит наверху, а не в сноске", async () => {
  const { карта } = await открыть_страницу();
  assert.equal(карта.caveat.hidden, false);
  const t = карта.caveat.textContent;
  assert.match(t, /Выбор есть не везде/);
  // 3 позиции, выбор у одной — значит у двух его нет, это 67 %.
  assert.match(t, /2 позиций из 3 \(67 %\)/);
  assert.match(t, /сведено 1 компаний из 3/);
});

test("карточка показывает четыре утверждения об изготовителе раздельно", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.equal(карта.card.hidden, false);
  assert.equal(карта.list.hidden, true);
  for (const подпись of ["Изготовитель по каталогу", "Назвал поставщик в файле",
                         "Бренд на карточке запроса", "Чей это номер на самом деле"]) {
    assert.ok(t.includes(подпись), "нет плитки «" + подпись + "»");
  }
  // Значения разные и не слиты: каталог знает SKF, поставщик назвал CHINA-BRG.
  assert.ok(t.includes("SKF"));
  assert.ok(t.includes("CHINA-BRG"));
  assert.ok(t.includes("180205 · ГПЗ"));
  // Источник утверждения подписан — без этого плитки неразличимы по смыслу.
  assert.match(t, /Слово поставщика из его же КП/);
  assert.match(t, /Проставлен нашим сотрудником/);
});

test("цена не печатается без кода валюты", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  assert.match(карта.card.textContent, /100 EUR/);
  // Вторая позиция: цена есть, валюты нет — это обязано быть сказано словами.
  карта.card.textContent = "";
  await открыть_страницу().then(async ({ карта: к2 }) => {
    await к2.rows.children[1].querySelectorAll("button")[0].fire("click");
    assert.match(к2.card.textContent, /50 \(валюта не названа\)/);
  });
});

test("имя компании берётся из companies, а не из каждого предложения", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  // В предложениях имени нет вовсе: если страница читает его оттуда, здесь будет
  // пусто, и таблица предложений потеряет первую колонку.
  assert.ok(t.includes("Учебный завод"), "имя компании не разрешилось из companies");
});

test("несведённая компания ведёт прямой ссылкой в Битрикс", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const ссылки = карта.card.querySelectorAll("a");
  const в_битрикс = ссылки.filter((a) => String(a.href || "").includes("bitrix24"));
  assert.equal(в_битрикс.length, 1, "ссылки на карточку Битрикса нет");
  assert.match(в_битрикс[0].href, /crm\/company\/details\/777\/$/);
  assert.match(карта.card.textContent, /не сведена с реестром/);
});

test("закрытое правом поле показано как «нет доступа», а не как пустота", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  assert.match(карта.card.textContent, /нет доступа/i);
});

test("позиция вне каталога объясняет пустые разделы, а не молчит", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[1].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.match(t, /Позиции нет в каталоге деталей/);
  assert.match(t, /Это отсутствие связи, а не отсутствие аналогов/);
  assert.match(t, /Сравнивать не с чем/);
});

test("пустой снимок — это «пока пусто», а не поломка", async () => {
  const { карта } = await открыть_страницу({ version: 1, positions: [], companies: [],
                                             totals: {} });
  assert.equal(карта.status.hidden, false);
  assert.match(карта.status.textContent, /Пока пусто/);
  assert.equal(карта.list.hidden, true);
});

test("отказ по праву объясняется, а не показывает белый экран", async () => {
  const { карта } = await открыть_страницу(null,
    new Response("{}", { status: 403, headers: { "Content-Type": "application/json" } }));
  assert.equal(карта.status.hidden, false);
  assert.match(карта.status.textContent, /нет права suppliers/);
  assert.match(карта.status.textContent, /Доступы/);
});
