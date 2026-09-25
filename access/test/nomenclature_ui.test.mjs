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
import test from "node:test";
import assert from "node:assert/strict";
// Мини-DOM вынесен в общий модуль: та же подделка понадобилась странице
// счётчиков, а две расходящиеся подделки DOM не заметит никто.
import { открыть } from "./minidom.mjs";

const html = fs.readFileSync(new URL("../../public/nomenclature.html", import.meta.url), "utf8");

const СНИМОК = {
  version: 1,
  totals: { positions: 3, with_choice: 1, comparable: 1, in_catalog: 1,
            companies: 3, companies_resolved: 1, no_demand: 1 },
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
      // КЛЮЧИ ПРЕДЛОЖЕНИЯ ОДНОБУКВЕННЫЕ — таблица в library/crossref.py
      // (ПОЛЯ_ПРЕДЛОЖЕНИЯ). Снимок это провод, а не документ: имена ключей
      // стоили 2,4 МиБ из 6,9, и их сокращение вернуло запас под условия.
      //   c=компания e=сущность m=изготовитель из файла b=бренды p=цена u=валюта
      //   q=количество n=единица s=базис l=срок поставки k=срок изготовления
      //   y=оплата a=аванс t=сумма r=пометки источника f=карточка v=уверенность d=дата
      //
      // ТРИ СОСТОЯНИЯ УСЛОВИЙ, и все три обязаны выглядеть по-разному:
      //   первое предложение — условия прочитаны («сссф», срок изготовления из файла);
      //   второе — разбор проверил и не нашёл («нннн» = верифицированное отсутствие);
      //   третье — пометок нет вовсе, то есть разбор до условий не дошёл.
      list: [
        { c: "101", e: "KV-S-000001-8", p: 100, u: "EUR", q: 4,
          n: "шт", s: "EXW", l: 30, k: 45, y: "LC, 30 на 70", a: 30, t: 400,
          r: "сссф", b: ["SKF"], m: "CHINA-BRG", d: "2026-09-01" },
        { c: "777", p: 120, u: "EUR", r: "нннн", d: "2026-09-02" },
        { c: "101", e: "KV-S-000001-8",
          p: { "закрыто": "suppliers_fin" }, u: "EUR", d: "2026-09-03" },
      ] },    { k: "sealkit12", n: "SEAL-KIT-12", name: "Комплект уплотнений", co: 1, offers: 1,
      shown: 1, cmp: false, cat: false, oem_file: [], oem_cat: null, brands: [],
      // Спрос одной сделкой в одной единице: сумма законна. Выбора нет — это и
      // есть строка списка работы.
      demand: { deals: 1, rows: 2, qty: 10, units: 1, unit_name: "шт" },
      alts: [], models: [], makers: [],
      list: [{ c: "102", p: 50, d: "2026-09-01" }] },
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
  return открыть(html, { снимок, ответ });
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
  // sealkit12: одна сделка, одна единица — количество печатается ВМЕСТЕ с её
  // именем. Голое «10» читается как штуки, метры и килограммы одинаково.
  const вторая = строки[1].textContent;
  assert.match(вторая, /1 сделка/);
  assert.match(вторая, /· 10 шт/);
  // oring5: спроса нет вовсе.
  assert.match(строки[2].textContent, /Не спрашивали/);
  // Итог «нет в спросе» показывается только когда такие позиции есть: в корпусе
  // это oring5. На живой базе их ноль, и клетка не занимает места.
  assert.match(карта.totals.textContent, /нет в спросе/);
});

test("отбор «одно предложение» — это список работы, и он один", async () => {
  const { карта } = await открыть_страницу();
  const кнопки = карта.tabs.querySelectorAll("button");
  const нужная = кнопки.find((b) => b.textContent.includes("запросить второго"));
  assert.ok(нужная, "отбора со списком работы нет");
  // Отдельного отбора «спрашивали, а выбора нет» быть не должно: он совпадает
  // с этим, и два отбора на одно множество читаются как два разных множества.
  assert.equal(кнопки.filter((b) => b.textContent.includes("Спрашивали")).length, 0);
  // В корпусе таких две: sealkit12 и oring5. У 6205 выбор из двух компаний.
  assert.match(нужная.textContent, /· 2$/);
  await нужная.fire("click");
  assert.equal(карта.rows.children.length, 2);
  // Порядок по спросу: sealkit12 со спросом впереди oring5 без спроса.
  assert.match(карта.rows.children[0].textContent, /SEAL-KIT-12/);
  assert.match(карта.rows.children[1].textContent, /O-RING-5/);
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

// ── КОММЕРЧЕСКИЕ УСЛОВИЯ И ПРИЧИНА ИХ ОТСУТСТВИЯ ──────────────────────────
// Владелец просил читать из каждого КП базис, оплату, срок производства и срок
// поставки, а отсутствие помечать ВЕРИФИЦИРОВАННЫМ. Разбор это делает; здесь
// проверяется, что закупщик видит разницу между «поставщик не назвал» (можно
// спросить) и «наш разбор не дошёл» (чинить нам). Прочерк без причины путал их.

test("условия предложения показаны, включая срок изготовления отдельно", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.match(t, /EXW/, "базис не показан");
  assert.match(t, /LC, 30 на 70/, "условия оплаты не показаны");
  assert.match(t, /аванс 30/, "доля аванса не показана");
  assert.match(t, /45/, "срок изготовления не показан");
  // Заголовки колонок обязаны различать два срока: «Срок, дн.» на оба — это
  // приглашение сравнить срок производства одного КП со сроком поставки другого.
  assert.match(t, /Изготовл\., дн\./);
  assert.match(t, /Поставка, дн\./);
  assert.match(t, /Сумма/, "суммы строки нет — «цена × количество» не проверить");
});

test("«в КП не указано» и «разбор не дошёл» — разные надписи", async () => {
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  // Второе предложение: пометки «нннн» — разбор проверил и условий не нашёл.
  assert.match(t, /в КП не указано/,
    "верифицированное отсутствие показано как обычный прочерк");
  // Третье предложение: пометок нет вовсе — разбор до условий не дошёл.
  assert.match(t, /разбор не дошёл/,
    "наш недочёт разбора выглядит как молчание поставщика");
});

test("условие из общих условий КП подписано как таковое", async () => {
  // Срок изготовления первого предложения помечен «ф» — взят из общих условий
  // файла, а не из строки. К этой строке он мог и не относиться, и об этом надо
  // сказать, иначе число выглядит как обещание по этой позиции.
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  assert.match(карта.card.textContent, /из общих условий КП/);
});

test("раздел машин не утверждает установку", async () => {
  // Связь «деталь ↔ машина» получена разбором свободного текстового поля. Это
  // «рядом с деталью написано имя машины», а не «применимость подтверждена» и не
  // «деталь установлена». Раздел назывался «Где стоит» и утверждал установку,
  // которой в данных нет ни одной строкой.
  const { карта } = await открыть_страницу();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.match(t, /Для каких машин запрашивали/);
  assert.doesNotMatch(t, /Где стоит/, "заголовок снова утверждает установку");
  assert.match(t, /Подтверждённой применимости и факта установки .* нет/s);
});

test("дата снимка видна, а её отсутствие названо", async () => {
  const с_датой = await открыть_страницу({ ...СНИМОК, published_at: "2026-09-20T10:00:00Z" });
  assert.match(с_датой.карта.published.textContent, /2026-09-20 10:00 UTC/);
  // Без даты — не пустота, а прямая надпись: иначе вчерашний снимок выглядит
  // как сегодняшний.
  const без = await открыть_страницу();
  assert.match(без.карта.published.textContent, /Дата сборки неизвестна/);
});

test("количество без названной единицы подписано, а не оставлено голым числом", async () => {
  // В строках спроса единицу проставляют не всегда: units = 0 значит «ни в одной
  // строке единицы нет». Прежде страница печатала в этом случае просто «· 480»,
  // и число читалось как штуки — хотя это могли быть метры. Молчание тут хуже
  // оговорки: оговорку видно, а подставленную единицу — нет.
  const снимок = structuredClone(СНИМОК);
  снимок.positions[1].demand = { deals: 1, rows: 2, qty: 480, units: 0 };
  const { карта } = await открыть_страницу(снимок);
  assert.match(карта.rows.children[1].textContent, /· 480 — единица не указана/);
});

test("подпись источника условия не слипается с числом", async () => {
  // Правило .src стояло под .claim, поэтому в плитке подпись шла со своей строки,
  // а в ЯЧЕЙКЕ ТАБЛИЦЫ оставалась строчной: «30» и «из общих условий КП»
  // сливались в «30из общих условий КП», и срок читался как трёхзначный.
  // Разметку этим не проверить — текст в обоих случаях один; проверяется стиль.
  //
  // Блоков <style> на странице больше одного, и правило лежит НЕ в первом:
  // первая версия проверки брала только первый и сказала «правила нет вовсе»,
  // хотя оно было. Комментарии снимаются до поиска — пояснение про «.src{»
  // нашлось бы раньше настоящего правила (CLAUDE.md, разбор читает код).
  const блоки = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
  assert.ok(блоки.length, "на странице нет ни одного блока стилей");
  const стиль = блоки.join("\n").replace(/\/\*[\s\S]*?\*\//g, "");
  // Ищем не «правило, где упомянут .src», а правило, которое действует В ЯЧЕЙКЕ
  // ТАБЛИЦЫ: «.claim .src{display:block}» упоминание содержит, а ячейку не
  // покрывает — на нём первая версия проверки и осталась зелёной.
  const годится = стиль.split("}").some((кусок) => {
    const [селектор, тело] = [кусок.slice(0, кусок.indexOf("{")), кусок.slice(кусок.indexOf("{") + 1)];
    if (кусок.indexOf("{") < 0 || !/display\s*:\s*block/.test(тело)) return false;
    return селектор.split(",").some((s) => /^\s*(td\s+|table\s+)?\.src\s*$/.test(s));
  });
  assert.ok(годится,
    "подпись источника не объявлена блочной вне плитки: в ячейке таблицы слипнётся с числом");
});

// ── СНИМОК ПО ЧАСТЯМ (24.09.2026) ─────────────────────────────────────────
// Единый crossref:v1 перестал помещаться в 8 МиБ (прогон 35986620488: 10,51 МиБ)
// и разложен library/crossref.разложить: заголовок, части списка и корзины
// подробностей. Здесь та же разметка в той же форме, что пишет публикатор:
// строка списка без ПОДРОБНОСТЕЙ и с номером корзины b, подробности — в корзине.

const ПОДРОБНОСТИ = ["list", "shown", "makers", "cat_name", "category", "unit"];
const СБОРКА = "2026-09-24T03:00:00Z";

function разложить(снимок, { частей = 2, сборка_части = СБОРКА, сборка_корзины = СБОРКА } = {}) {
  const { positions, ...заголовок } = снимок;
  const строки = [];
  const корзины = {};
  positions.forEach((p, i) => {
    const b = 3 + (i % 2);                  // две корзины на три позиции: одна общая
    const строка = { b };
    const подробно = {};
    for (const [k, v] of Object.entries(p)) (ПОДРОБНОСТИ.includes(k) ? подробно : строка)[k] = v;
    строки.push(строка);
    (корзины[b] ||= { version: 1, part: b, published_at: сборка_корзины, positions: {} })
      .positions[p.k] = подробно;
  });
  const части = [];
  const размер = Math.ceil(строки.length / частей);
  for (let i = 0; i < частей; i++) {
    части.push({ version: 1, part: i, published_at: сборка_части,
                 positions: строки.slice(i * размер, (i + 1) * размер) });
  }
  return { заголовок: { ...заголовок, published_at: СБОРКА, lists: частей, parts: 32 }, части, корзины };
}

async function открыть_частями(опции = {}, { hash = "", сбой_корзины = false } = {}) {
  const { заголовок, части, корзины } = разложить(СНИМОК, опции);
  const запросы = [];
  const маршруты = (url) => {
    запросы.push(url);
    if (url === "/api/crossref") return заголовок;
    let m = url.match(/^\/api\/crossref\/rows\?l=(\d\d)$/);
    if (m) return части[Number(m[1])];
    m = url.match(/^\/api\/crossref\/offers\?b=(\d\d)$/);
    if (m) {
      if (сбой_корзины) throw new Error("сервер ответил 503");
      return корзины[Number(m[1])] || { version: 1, part: Number(m[1]), positions: {} };
    }
    throw new Error("неожиданный адрес " + url);
  };
  const страница = await открыть(html, { маршруты, hash });
  return { ...страница, запросы };
}

test("список склеивается из частей в исходном порядке", async () => {
  const { карта, запросы } = await открыть_частями();
  assert.equal(карта.status.hidden, true, "плашка загрузки осталась на экране");
  assert.equal(карта.rows.children.length, 3);
  assert.match(карта.rows.children[0].textContent, /6-205/);
  assert.match(карта.rows.children[1].textContent, /SEAL-KIT-12/);
  assert.match(карта.rows.children[2].textContent, /O-RING-5/);
  assert.match(карта.count.textContent, /3 из 3/);
  // Для списка корзины не нужны: ни одного запроса подробностей до открытия карточки.
  assert.deepEqual(запросы, ["/api/crossref", "/api/crossref/rows?l=00", "/api/crossref/rows?l=01"]);
  // Поиск по номеру аналога работает без корзины: аналоги лежат в строке.
  карта.search.value = "180205";
  await карта.search.fire("input");
  assert.equal(карта.rows.children.length, 1);
});

test("карточка дочитывает свою корзину и показывает предложения и исполнителей", async () => {
  const { карта, запросы } = await открыть_частями();
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.ok(запросы.includes("/api/crossref/offers?b=03"), "корзина позиции не запрошена");
  assert.match(t, /100 EUR/);
  assert.match(t, /Учебный завод/, "имя компании не разрешилось из companies заголовка");
  assert.match(t, /Кто это делает и в какой роли/, "реестр исполнителей не приехал из корзины");
  assert.match(t, /в КП не указано/);
  assert.match(t, /180205 · ГПЗ/);
  assert.doesNotMatch(t, /снимок обновляется|не прочитались/);
});

test("корзина читается один раз на все её позиции", async () => {
  const { карта, запросы } = await открыть_частями();
  // 6205 и O-RING-5 лежат в корзине 03.
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  await карта.rows.children[2].querySelectorAll("button")[0].fire("click");
  assert.equal(запросы.filter((u) => u === "/api/crossref/offers?b=03").length, 1);
});

test("прямой адрес позиции открывает карточку с предложениями", async () => {
  const { карта } = await открыть_частями({}, { hash: "#k=6205" });
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(карта.card.hidden, false);
  assert.match(карта.card.textContent, /100 EUR/);
});

test("часть списка другой сборки — отказ со словами, а не смесь", async () => {
  const { карта } = await открыть_частями({ сборка_части: "2026-09-23T03:00:00Z" });
  assert.equal(карта.status.hidden, false);
  assert.match(карта.status.textContent, /снимок обновляется/);
  assert.equal(карта.list.hidden, true);
});

test("корзина другой сборки показывается с оговоркой", async () => {
  const { карта } = await открыть_частями({ сборка_корзины: "2026-09-23T03:00:00Z" });
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.match(t, /100 EUR/);
  assert.match(t, /снимок обновляется/);
  assert.match(t, /2026-09-23 03:00 UTC/);
});

test("корзина не прочиталась — карточка сказала об этом, а не показала пустоту", async () => {
  const { карта } = await открыть_частями({}, { сбой_корзины: true });
  await карта.rows.children[0].querySelectorAll("button")[0].fire("click");
  const t = карта.card.textContent;
  assert.equal(карта.card.hidden, false);
  assert.match(t, /Предложения не прочитались/);
  // Строка списка всё равно на экране: изготовитель по каталогу и спрос.
  assert.match(t, /SKF/);
});

// ── ПОЗИЦИЯ — ПАРА «БРЕНД + КОД» (правила номенклатуры П1, П2; 25.09.2026) ──
// Владелец: «код может стоять отдельно, но в соседнем столбце обязательно должен
// быть бренд». Снимок несёт у пары бренд (bk, bn) и его источник (bs), у пары без
// бренда — причину (bw) и кандидатов спора (bc); итоги — totals.brand. Код у двух
// брендов — две строки с одним k: SKF 6205 и FAG 6205.

const ПАРЫ = {
  version: 1,
  totals: { positions: 5, with_choice: 0, comparable: 0, in_catalog: 1, companies: 2,
            companies_resolved: 1, no_demand: 5,
            brand: { codes: 4, determined: 3, by: { "каталог": 1, "строка": 1, "карточка": 0, "маска": 1 },
                     none: 1, disputed: 1 } },
  positions: [
    { k: "6205", bk: "skf", bn: "SKF", bs: "каталог", n: "6205", name: "Подшипник учебный", co: 1,
      offers: 2, shown: 2, cat: true, oem_cat: "SKF", brands: [], oem_file: ["SKF"],
      list: [{ c: "101", p: 10, u: "EUR", m: "SKF", o: "с:skf" }, { c: "102", p: 12, u: "EUR" }] },
    { k: "6205", bk: "fag", bn: "FAG", bs: "строка", n: "6205", name: "Подшипник учебный", co: 1,
      offers: 1, shown: 1, cat: true, oem_cat: "SKF", oem_file: ["FAG"],
      list: [{ c: "102", p: 11, u: "EUR", m: "FAG", o: "с:fag" }] },
    { k: "4088833", bk: "cummins", bn: "Cummins", bs: "маска", n: "4088833", name: "Фильтр учебный Cummins",
      co: 1, offers: 1, shown: 1, list: [{ c: "101", p: 5, u: "USD" }] },
    { k: "7001234", bw: "нет", n: "7001234", name: "Фильтр учебный", co: 1, offers: 1, shown: 1,
      list: [{ c: "101", p: 6, u: "USD" }] },
    { k: "kv4417b", bw: "спорно", bc: [["SKF", "строка"], ["FAG", "строка"]], n: "KV-4417-B",
      name: "Уплотнение учебное", co: 1, offers: 1, shown: 1, list: [{ c: "102", p: 7, u: "USD" }] },
  ],
  companies: [{ co: "101", ent: "KV-S-000001-8", name: "Учебный завод", rows: 3, parts: 3 },
              { co: "102", ent: null, name: null, rows: 3, parts: 3 }],
};

async function открыть_пары(hash = "") {
  const маршруты = (url) => {
    if (url === "/api/crossref") return ПАРЫ;
    throw new Error("неожиданный адрес " + url);
  };
  return открыть(html, { маршруты, hash });
}

test("бренд и код одной строкой, источник мелко, счётчик бренда наверху", async () => {
  const { карта } = await открыть_пары();
  // Счётчик — первой плиткой: «3 из 5 с определённым брендом (60 %)».
  assert.match(карта.totals.children[0].textContent, /3 из 5с определённым брендом \(60 %\)/);
  // Основной список — только пары с брендом, по одной строке на пару.
  assert.equal(карта.rows.children.length, 3);
  const ячейки = карта.rows.children.map((tr) => tr.children[0].textContent);
  assert.ok(ячейки.some((t) => /^SKF 6205каталог · у кода ещё 1 пара$/.test(t)), ячейки.join(" | "));
  assert.ok(ячейки.some((t) => /^FAG 6205строка спецификации или КП/.test(t)), ячейки.join(" | "));
  assert.ok(ячейки.some((t) => /^Cummins 4088833по маске кода — предположение$/.test(t)), ячейки.join(" | "));
  // Папки: с брендом и «Бренд не определён» — отдельно от основного списка.
  const папки = карта.folders.querySelectorAll("button");
  assert.deepEqual(папки.map((b) => b.textContent),
    ["С брендом · 3 из 5 (60 %)", "Бренд не определён · 2"]);
  assert.match(карта["folder-note"].textContent,
    /каталог 1 · строка спецификации или КП 1 · карточка запроса 0 · маска кода \(предположение\) 1/);
});

test("папка «Бренд не определён» с причиной: не назван никем или спорно с обоими", async () => {
  const { карта } = await открыть_пары();
  await карта.folders.querySelectorAll("button")[1].fire("click");
  assert.equal(карта.rows.children.length, 2);
  const t = карта.rows.children.map((tr) => tr.children[0].textContent).join(" | ");
  assert.match(t, /7001234бренд не определён: не назван никем/);
  assert.match(t, /KV-4417-Bспорно: SKF \(строка спецификации или КП\) · FAG \(строка спецификации или КП\)/);
  assert.match(карта["folder-note"].textContent, /не дали \(1\).*«спорно» \(1\)/);
  // Поиск видит и другую папку и говорит об этом, а не молчит.
  карта.search.value = "6205";
  await карта.search.fire("input");
  assert.equal(карта.rows.children.length, 0);
  assert.match(карта.count.textContent, /ещё 2 в папке «С брендом»/);
});

test("адрес кода, разбитого по брендам, показывает все его пары", async () => {
  const { карта } = await открыть_пары("#k=6205");
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(карта.card.hidden, true);
  assert.equal(карта.rows.children.length, 2);
  assert.equal(карта["code-filter"].hidden, false);
  assert.match(карта["code-filter"].textContent, /Код 6205 — 2 позиции/);
});

test("адрес пары открывает её карточку, соседние пары кода — кнопками", async () => {
  const { карта } = await открыть_пары("#k=6205~skf");
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  assert.equal(карта.card.hidden, false);
  const t = карта.card.textContent;
  assert.match(t, /SKF 6205/);
  assert.match(t, /Бренд SKF — по каталогу/);
  assert.match(t, /FAG 6205 · 1 предложение/);
  // Предложение без своего бренда в паре SKF: оригинал не подтверждён — сказано.
  assert.match(t, /бренд в этом КП не назван/);
});

test("снимок до правила «бренд + код» — без папок и без выдуманных причин", async () => {
  const { карта } = await открыть_страницу();
  assert.equal(карта.folders.hidden, true);
  assert.equal(карта.rows.children.length, 3);
  assert.doesNotMatch(карта.rows.textContent, /бренд не определён/);
});
