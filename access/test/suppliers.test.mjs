// Синтетические данные: ни строки из CRM, ни производственных ключей.
// Проверяется вход в раздел по тонкому праву и серверная резка полей —
// «скрыто стилями» тут не считается защитой, закрытого значения в ответе
// быть не должно вовсе.
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../../public/_worker.js";
import { defaultAcl } from "../acl.js";

const ORIGIN = "https://portal.example.test";
const TEAM = "suppliers-test.cloudflareaccess.com";
const OWNER = "owner@example.test";
const READER = "reader@example.test";     // право suppliers, без контактов и денег
const FINANCE = "finance@example.test";   // suppliers + suppliers_fin
const GUEST = "guest@example.test";       // без прав вовсе
const encoder = new TextEncoder();
const b64 = (v) => Buffer.from(v).toString("base64url");
const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "suppliers-key", alg: "RS256" };
async function token(email) {
  const input = b64(JSON.stringify({ alg: "RS256", kid: jwk.kid })) + "." +
    b64(JSON.stringify({ email, iss: `https://${TEAM}`, exp: Math.floor(Date.now() / 1000) + 600 }));
  return input + "." + b64(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, encoder.encode(input)));
}

// В снимке намеренно лежат и контакты, и деньги, и вложенный объект: резка обязана
// доставать поле на любой глубине, иначе право обходится одним уровнем вложенности.
const SNAPSHOT = {
  version: 1,
  published_at: "2026-09-20T10:00:00Z",
  totals: { entities: 2, numbered: 2, held: 0 },
  entities: [
    { number: "KV-S-000001-8", name: "Учебный завод", domain: "example.test", inn: "0000000000",
      sources: ["synthetic"], merged_by: "domain",
      emails: ["nobody@example.test"], phones: ["+0 000 000-00-00"],
      persons: [{ name: "Вымышленное Лицо", emails: ["nobody@example.test"] }],
      terms: "предоплата 30 %", spend: 1234,
      lots: [{ id: "L-1", payment: "аккредитив", price: 10 }] },
    { number: "KV-S-000002-6", name: "Учебная мастерская", domain: "example.invalid", inn: null,
      sources: ["synthetic"], merged_by: "name" },
  ],
};

function envFor(snapshot = SNAPSHOT) {
  const box = new Map();
  const assets = [];
  const acl = defaultAcl();
  acl.defaultRole = "guest";
  acl.users[READER] = { role: "guest", sites: [], tabs: [], rights: ["suppliers"] };
  acl.users[FINANCE] = { role: "guest", sites: [], tabs: [], rights: ["suppliers", "suppliers_fin"] };
  box.set("acl:v1", JSON.stringify(acl));
  if (snapshot) box.set("suppliers:v1", JSON.stringify(snapshot));
  return { CF_ACCESS_TEAM: TEAM, __certs: { keys: [jwk] }, ADMIN_EMAILS: OWNER, assets,
    ACL: { box, get: async (key, options) => {
      const raw = box.get(key) ?? null;
      return raw !== null && options?.type === "json" ? JSON.parse(raw) : raw;
    }, put: async (key, value) => { box.set(key, value); },
      list: async ({ prefix = "", limit = 100, cursor = "0" }) => {
        const all = [...box.keys()].filter((key) => key.startsWith(prefix)).sort();
        const start = Number(cursor);
        return { keys: all.slice(start, start + limit).map((name) => ({ name })),
          list_complete: all.length <= start + limit, cursor: String(start + limit) };
      } },
    ASSETS: { fetch: async (request) => {
      assets.push(new URL(request.url).pathname);
      return new Response("<!doctype html><title>Suppliers fixture</title><main>UI SHELL</main>",
        { headers: { "Content-Type": "text/html" } });
    } },
  };
}
const ctx = { waitUntil: (promise) => Promise.resolve(promise).catch(() => {}) };
async function call(env, path, email = READER, init = {}) {
  const headers = new Headers(init.headers);
  if (email) headers.set("Cf-Access-Jwt-Assertion", await token(email));
  return worker.fetch(new Request(ORIGIN + path, { ...init, headers }), env, ctx);
}

test("без подписи Access раздел не открывается ни страницей, ни API", async () => {
  const env = envFor();
  for (const path of ["/suppliers", "/suppliers/", "/suppliers.html", "/api/suppliers"]) {
    const response = await call(env, path, null);
    assert.equal(response.status, 403, path);
    assert.doesNotMatch(await response.text(), /Учебный завод|UI SHELL/);
  }
  // Подпись, испорченная на последних символах, тоже не проходит.
  const fake = await token(OWNER);
  const broken = await call(env, "/api/suppliers", null,
    { headers: { "Cf-Access-Jwt-Assertion": fake.slice(0, -6) + "AAAAAA" } });
  assert.equal(broken.status, 403);
  assert.deepEqual(env.assets, []);
});

test("право suppliers решает вход, а не список сайтов", async () => {
  const env = envFor();
  for (const path of ["/suppliers", "/api/suppliers"]) {
    const deny = await call(env, path, GUEST);
    assert.equal(deny.status, 403, path);
    assert.equal((await deny.json()).error, "forbidden");
    const allow = await call(env, path, READER);
    assert.equal(allow.status, 200, path);
  }
  // Гостю страница из ASSETS не отдавалась: единственное обращение — за READER.
  assert.deepEqual(env.assets, ["/suppliers.html"]);
});

test("контакты и деньги вырезаются на сервере, а не прячутся на странице", async () => {
  const env = envFor();
  const body = await (await call(env, "/api/suppliers", READER)).text();
  // Закрытых значений в ответе нет ни в каком виде: ни строкой, ни в глубине.
  for (const secret of ["nobody@example.test", "+0 000 000-00-00", "Вымышленное Лицо",
                        "предоплата 30 %", "аккредитив", "1234"]) {
    assert.ok(!body.includes(secret), `в ответе осталось «${secret}»`);
  }
  const value = JSON.parse(body);
  const first = value.entities[0];
  assert.deepEqual(first.emails, { закрыто: "suppliers_pii" });
  assert.deepEqual(first.persons, { закрыто: "suppliers_pii" });
  assert.deepEqual(first.terms, { закрыто: "suppliers_fin" });
  assert.deepEqual(first.spend, { закрыто: "suppliers_fin" });
  // Вложенное поле режется на любой глубине.
  assert.deepEqual(first.lots[0].payment, { закрыто: "suppliers_fin" });
  assert.equal(first.lots[0].price, 10);
  // Открытые поля не трогаются.
  assert.equal(first.name, "Учебный завод");
  assert.equal(first.number, "KV-S-000001-8");
  assert.deepEqual(value.rights, ["suppliers"]);
});

test("своё право открывает свою группу полей и только её", async () => {
  const env = envFor();
  const value = await (await call(env, "/api/suppliers", FINANCE)).json();
  const first = value.entities[0];
  assert.equal(first.terms, "предоплата 30 %");
  assert.equal(first.spend, 1234);
  assert.equal(first.lots[0].payment, "аккредитив");
  assert.deepEqual(first.emails, { закрыто: "suppliers_pii" });
  assert.deepEqual(value.rights, ["suppliers", "suppliers_fin"]);
});

test("владельцу отдаётся снимок без резки", async () => {
  const env = envFor();
  const value = await (await call(env, "/api/suppliers", OWNER)).json();
  assert.equal(value.admin, true);
  assert.deepEqual(value.entities[0].emails, ["nobody@example.test"]);
  assert.equal(value.entities[0].spend, 1234);
});

test("снимка ещё нет — это «нет данных», а не поломка", async () => {
  const env = envFor(null);
  const response = await call(env, "/api/suppliers", READER);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.deepEqual(value.entities, []);
  assert.equal(value.published_at, null);
});

test("страница отдаётся с запретом кеша и индексации", async () => {
  const env = envFor();
  const response = await call(env, "/suppliers", READER);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("Content-Type"), /text\/html/);
  assert.equal(response.headers.get("Cache-Control"), "private, no-store, max-age=0");
  assert.match(response.headers.get("X-Robots-Tag"), /noindex/);
  assert.equal(response.headers.get("X-Frame-Options"), "DENY");
  assert.match(response.headers.get("Content-Security-Policy"), /frame-ancestors 'none'/);
  assert.deepEqual(env.assets, ["/suppliers.html"]);
});

test("альтернативное написание пути не обходит проверку права", async () => {
  const env = envFor();
  // Всё это ASSETS обслужил бы сам, если бы маршрут их не перехватывал.
  for (const path of ["/suppliers/all", "/api/suppliers/list", "/admin/suppliers",
                      "/suppliers%2f", "/%73uppliers", "/suppliers;x"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  assert.deepEqual(env.assets, []);
});

test("менять раздел запросом нельзя: только GET", async () => {
  const env = envFor();
  for (const method of ["POST", "PUT", "DELETE"]) {
    const response = await call(env, "/api/suppliers", OWNER, { method,
      headers: { "Content-Type": "application/json" }, body: "{}" });
    assert.equal(response.status, 405, method);
  }
});

test("плашка на портале показывается по праву, а не по списку сайтов", async () => {
  const env = envFor();
  const есть = await (await call(env, "/", READER)).text();
  assert.match(есть, /href="\/suppliers"/);
  const нет = await (await call(env, "/", GUEST)).text();
  assert.doesNotMatch(нет, /href="\/suppliers"/);
  const владелец = await (await call(env, "/", OWNER)).text();
  assert.match(владелец, /href="\/suppliers"/);
});

// ─── Номенклатура: карточка товара под тем же правом ─────────────────────────
// Раздел добавлен 22.09.2026 по ТЗ владельца. Он показывает по позиции, КТО
// давал предложение и по какой цене, — те же коммерческие сведения, что и
// реестр, прочитанные с другой стороны. Отдельного права у него нет намеренно:
// два замка на один секрет решаются слабейшим.

const CROSSREF = {
  version: 1,
  totals: { positions: 1, with_choice: 1, companies: 2, companies_resolved: 1 },
  positions: [
    { k: "6205", n: "6-205", name: "Подшипник учебный", co: 2, offers: 2, shown: 2,
      cmp: true, oem_file: ["CHINA-BRG"], oem_cat: "SKF", brands: ["SKF"],
      alts: [{ pn: "180205", kind: "номер изготовителя", maker: "ГПЗ" }],
      models: ["SGT-400"], makers: [{ name: "Учебный завод", role: "OEM" }],
      list: [
        { co: "101", ent: "KV-S-000001-8", ent_name: "Учебный завод", price: 100,
          cur: "EUR", lead: 30, rfq: "RFQ-1", terms: "предоплата 30 %" },
        { co: "102", ent: null, ent_name: null, price: 120, cur: "EUR",
          emails: ["nobody@example.test"] },
      ] },
  ],
  companies: [
    { co: "101", ent: "KV-S-000001-8", name: "Учебный завод", rows: 1, parts: 1,
      brands: ["SKF"], list: [{ k: "6205", n: "6-205", cnt: 1, price: 100, cur: "EUR" }] },
  ],
};

function envCross(snapshot = CROSSREF) {
  const env = envFor();
  if (snapshot) env.ACL.box.set("crossref:v1", JSON.stringify(snapshot));
  return env;
}

test("номенклатура закрыта без подписи Access", async () => {
  const env = envCross();
  for (const path of ["/nomenclature", "/nomenclature/", "/nomenclature.html",
                      "/api/crossref"]) {
    const response = await call(env, path, null);
    assert.equal(response.status, 403, path);
    assert.doesNotMatch(await response.text(), /Подшипник учебный|UI SHELL/);
  }
  assert.deepEqual(env.assets, []);
});

test("номенклатуру открывает то же право suppliers", async () => {
  const env = envCross();
  for (const path of ["/nomenclature", "/api/crossref"]) {
    const deny = await call(env, path, GUEST);
    assert.equal(deny.status, 403, path);
    assert.equal((await deny.json()).error, "forbidden");
    const allow = await call(env, path, READER);
    assert.equal(allow.status, 200, path);
  }
  // Страница берётся своя, а не suppliers.html: иначе раздел открывался бы
  // чужой вёрсткой и молча показывал не те данные.
  assert.deepEqual(env.assets, ["/nomenclature.html"]);
});

test("резка полей действует и в номенклатуре", async () => {
  const env = envCross();
  const body = await (await call(env, "/api/crossref", READER)).text();
  for (const secret of ["nobody@example.test", "предоплата 30 %"]) {
    assert.ok(!body.includes(secret), `в ответе осталось «${secret}»`);
  }
  const value = JSON.parse(body);
  const предложения = value.positions[0].list;
  assert.deepEqual(предложения[0].terms, { закрыто: "suppliers_fin" });
  assert.deepEqual(предложения[1].emails, { закрыто: "suppliers_pii" });
  // Цена и валюта — не закрытые поля: ради них раздел и существует.
  assert.equal(предложения[0].price, 100);
  assert.equal(предложения[0].cur, "EUR");
});

test("снимка номенклатуры ещё нет — это «нет данных», а не поломка", async () => {
  const env = envFor();                      // crossref:v1 не положен
  const response = await call(env, "/api/crossref", READER);
  assert.equal(response.status, 200);
  const value = await response.json();
  assert.deepEqual(value.positions, []);
  assert.deepEqual(value.companies, []);
});

test("альтернативное написание пути номенклатуры не обходит проверку права", async () => {
  const env = envCross();
  for (const path of ["/nomenclature/all", "/api/crossref/list", "/nomenclature%2f",
                      "/%6eomenclature", "/nomenclature;x", "/api/crossref;x"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  assert.deepEqual(env.assets, []);
});

test("менять номенклатуру запросом нельзя: только GET", async () => {
  const env = envCross();
  for (const method of ["POST", "PUT", "DELETE"]) {
    const response = await call(env, "/api/crossref", OWNER, { method,
      headers: { "Content-Type": "application/json" }, body: "{}" });
    assert.equal(response.status, 405, method);
  }
});

test("плашка «Номенклатура» показывается по тому же праву", async () => {
  const env = envCross();
  const есть = await (await call(env, "/", READER)).text();
  assert.match(есть, /href="\/nomenclature"/);
  const нет = await (await call(env, "/", GUEST)).text();
  assert.doesNotMatch(нет, /href="\/nomenclature"/);
});

// ─────────────────────────────────────────────────────────────────────────────
// СЧЁТЧИКИ. Точки числовых замеров: только агрегаты, никаких позиций и компаний.
// Права здесь те же, что у номенклатуры, и проверяется это отдельно: соблазн
// «агрегаты же, пусть смотрят все» стоил бы раздачи того, насколько плохо мы
// закрываем спрос, шире, чем самой номенклатуры.
const СЧЁТЧИКИ = {
  version: 1,
  metrics: {
    "коды_и_цены": [
      { run: "111", at: "2026-09-21T01:00:00Z",
        nums: { asked: 100, with_kp: 10, other_feed: 2, no_price: 88, rows_asked: 400,
                rows_with: 40, rows_without: 340, price_codes: 11, price_rows: 20,
                price_asked: 10, price_in_catalog: 3, catalog: 50, catalog_priced: 3,
                catalog_asked: 12, plausible: 95, no_digit: 2, shorter_than_four: 1,
                longer_than_25: 2 } },
      { run: "222", at: "2026-09-22T01:00:00Z", note: "выдуманная оговорка",
        nums: { asked: 120, with_kp: 18, other_feed: 2, no_price: 100, rows_asked: 430,
                rows_with: 60, rows_without: 330, price_codes: 19, price_rows: 31,
                price_asked: 18, price_in_catalog: 4, catalog: 50, catalog_priced: 4,
                catalog_asked: 14, plausible: 112, no_digit: 3, shorter_than_four: 2,
                longer_than_25: 3 } },
    ],
  },
};

function envCounters(snapshot = СЧЁТЧИКИ) {
  const env = envFor();
  if (snapshot) env.ACL.box.set("counters:v1", JSON.stringify(snapshot));
  return env;
}

test("счётчики закрыты без подписи Access", async () => {
  const env = envCounters();
  for (const path of ["/counters", "/counters/", "/counters.html", "/api/counters"]) {
    const response = await call(env, path, null);
    assert.equal(response.status, 403, path);
  }
  assert.deepEqual(env.assets, []);
});

test("счётчики открывает то же право suppliers", async () => {
  const env = envCounters();
  for (const path of ["/counters", "/api/counters"]) {
    const deny = await call(env, path, GUEST);
    assert.equal(deny.status, 403, path);
    assert.equal((await deny.json()).error, "forbidden");
    const allow = await call(env, path, READER);
    assert.equal(allow.status, 200, path);
  }
  // Страница берётся своя, а не номенклатуры: один файл на два раздела уже
  // однажды открывал раздел чужой вёрсткой.
  assert.deepEqual(env.assets, ["/counters.html"]);
});

test("счётчики отдают все точки в порядке замера", async () => {
  const env = envCounters();
  const value = await (await call(env, "/api/counters", READER)).json();
  const точки = value.metrics["коды_и_цены"];
  assert.equal(точки.length, 2);
  assert.equal(точки[0].run, "111");
  assert.equal(точки[1].run, "222");
  // Оговорка доезжает до страницы: без неё «цифра упала» читается как провал,
  // а не как «замер сделан во время переразбора».
  assert.equal(точки[1].note, "выдуманная оговорка");
});

test("снимка счётчиков ещё нет — это «нет данных», а не поломка", async () => {
  const env = envFor();                      // counters:v1 не положен
  const response = await call(env, "/api/counters", READER);
  assert.equal(response.status, 200);
  assert.deepEqual((await response.json()).metrics, {});
});

test("битый снимок счётчиков — 503, а не тихая пустота", async () => {
  // Пустота на месте поломки — худший из ответов: страница скажет «пока пусто»,
  // и никто не узнает, что публикатор пишет мусор.
  for (const плохой of ['{"version":2,"metrics":{}}', "не json вовсе"]) {
    const env = envFor();
    env.ACL.box.set("counters:v1", плохой);
    const response = await call(env, "/api/counters", READER);
    assert.equal(response.status, 503, плохой.slice(0, 12));
    assert.equal((await response.json()).error, "suppliers_unavailable");
  }
});

test("альтернативное написание пути счётчиков не обходит проверку права", async () => {
  const env = envCounters();
  for (const path of ["/counters/all", "/api/counters/list", "/counters%2f",
                      "/%63ounters", "/counters;x", "/api/counters;x"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  assert.deepEqual(env.assets, []);
});

test("менять счётчики запросом нельзя: только GET", async () => {
  const env = envCounters();
  for (const method of ["POST", "PUT", "DELETE"]) {
    const response = await call(env, "/api/counters", OWNER, { method,
      headers: { "Content-Type": "application/json" }, body: "{}" });
    assert.equal(response.status, 405, method);
  }
});

test("плашка «Счётчики» показывается по тому же праву", async () => {
  const env = envCounters();
  const есть = await (await call(env, "/", READER)).text();
  assert.match(есть, /href="\/counters"/);
  const нет = await (await call(env, "/", GUEST)).text();
  assert.doesNotMatch(нет, /href="\/counters"/);
});

// ─────────────────────────────────────────────────────────────────────────────
// БРЕНДЫ И КОДЫ (этап 8.1). Под тем же правом suppliers: карточка бренда и цены
// по коду — те же коммерческие сведения, что номенклатура. Ключей несколько, и
// корзина кода выбирается номером из запроса: номер обязан превращаться в имя
// ключа только через закрытый список, иначе маршрут стал бы чтением любого KV.
const BRANDS = { version: 1, published_at: "2026-09-24T01:00:00Z",
  totals: { tiles: [{ id: "all", label: "кодов в базе всего", value: 10 }] },
  brands: [{ k: "skf", name: "Учебный бренд", dict: true }],
  suppliers: [{ k: "KV-S-000001-8", name: "Учебный завод", emails: ["nobody@example.test"] }] };
const LINKS = { version: 1, brands: ["skf"], suppliers: ["KV-S-000001-8"],
  codes: [["ab6205", "AB-6205", 3, [0], [0], 1]] };
const PAIRS = { version: 1, brands: ["skf"], suppliers: ["KV-S-000001-8"],
  pairs: [[0, 0, 1, 1, 0, 0, 0, 1, 2, "USD 1", 0]] };
const PART3 = { version: 1, part: 3, codes: { ab6205: { n: "AB-6205",
  offers: [{ s: "KV-S-000001-8", cur: "USD", min: 10, terms: "предоплата 30 %" }] } } };

function envBrands() {
  const env = envFor();
  env.ACL.box.set("brands:v1", JSON.stringify(BRANDS));
  env.ACL.box.set("brands:links:v1", JSON.stringify(LINKS));
  env.ACL.box.set("brands:pairs:v1", JSON.stringify(PAIRS));
  env.ACL.box.set("brands:codes:03", JSON.stringify(PART3));
  return env;
}
const BRAND_PATHS = ["/brands", "/api/brands", "/api/brands/links", "/api/brands/pairs",
                     "/api/brands/codes?b=03"];

test("бренды закрыты без подписи Access", async () => {
  const env = envBrands();
  for (const path of [...BRAND_PATHS, "/brands/", "/brands.html"]) {
    const response = await call(env, path, null);
    assert.equal(response.status, 403, path);
    assert.doesNotMatch(await response.text(), /Учебный бренд|UI SHELL|AB-6205/);
  }
  assert.deepEqual(env.assets, []);
});

test("бренды открывает то же право suppliers", async () => {
  const env = envBrands();
  for (const path of BRAND_PATHS) {
    const deny = await call(env, path, GUEST);
    assert.equal(deny.status, 403, path);
    assert.equal((await deny.json()).error, "forbidden");
    const allow = await call(env, path, READER);
    assert.equal(allow.status, 200, path);
  }
  // Страница — своя, а не номенклатуры или реестра.
  assert.deepEqual(env.assets, ["/brands.html"]);
});

test("каждый маршрут брендов отдаёт свой ключ", async () => {
  const env = envBrands();
  assert.equal((await (await call(env, "/api/brands", READER)).json()).brands[0].k, "skf");
  assert.equal((await (await call(env, "/api/brands/links", READER)).json()).codes[0][0], "ab6205");
  assert.equal((await (await call(env, "/api/brands/pairs", READER)).json()).pairs[0][9], "USD 1");
  const part = await (await call(env, "/api/brands/codes?b=03", READER)).json();
  assert.equal(part.part, 3);
  assert.equal(part.codes.ab6205.offers[0].min, 10);
});

test("корзина кода — только две цифры из закрытого диапазона", async () => {
  const env = envBrands();
  env.ACL.box.set("brands:codes:3", JSON.stringify(PART3));
  for (const b of ["3", "16", "99", "xx", "-1", "../acl", "03x", "", "acl:v1"]) {
    const response = await call(env, "/api/brands/codes?b=" + encodeURIComponent(b), READER);
    assert.equal(response.status, 400, b);
    assert.equal((await response.json()).error, "invalid_part");
  }
  const без = await call(env, "/api/brands/codes", READER);
  assert.equal(без.status, 400);
});

test("резка полей действует и в брендах", async () => {
  const env = envBrands();
  for (const path of ["/api/brands", "/api/brands/codes?b=03"]) {
    const body = await (await call(env, path, READER)).text();
    for (const secret of ["nobody@example.test", "предоплата 30 %"]) {
      assert.ok(!body.includes(secret), `${path}: в ответе осталось «${secret}»`);
    }
  }
  const part = await (await call(env, "/api/brands/codes?b=03", READER)).json();
  assert.deepEqual(part.codes.ab6205.offers[0].terms, { закрыто: "suppliers_fin" });
  // Цена КП не режется: ради неё раздел и существует.
  assert.equal(part.codes.ab6205.offers[0].min, 10);
});

test("снимка брендов ещё нет — «нет данных», а не поломка; битый — 503", async () => {
  const env = envFor();
  for (const [path, поле] of [["/api/brands", "brands"], ["/api/brands/links", "codes"],
                              ["/api/brands/pairs", "pairs"]]) {
    const response = await call(env, path, READER);
    assert.equal(response.status, 200, path);
    assert.deepEqual((await response.json())[поле], [], path);
  }
  const пусто = await (await call(env, "/api/brands/codes?b=15", READER)).json();
  assert.deepEqual(пусто.codes, {});
  for (const плохой of ['{"version":2}', "не json вовсе"]) {
    const e2 = envFor();
    e2.ACL.box.set("brands:v1", плохой);
    const response = await call(e2, "/api/brands", READER);
    assert.equal(response.status, 503, плохой);
  }
});

test("альтернативное написание пути брендов не обходит проверку права", async () => {
  const env = envBrands();
  for (const path of ["/brands/all", "/api/brands/list", "/brands%2f", "/%62rands", "/brands;x",
                      "/api/brands;x", "/api/brands/codes/03", "/api/brands/links/x"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  assert.deepEqual(env.assets, []);
});

test("менять бренды запросом нельзя: только GET", async () => {
  const env = envBrands();
  for (const method of ["POST", "PUT", "DELETE"]) {
    const response = await call(env, "/api/brands", OWNER, { method,
      headers: { "Content-Type": "application/json" }, body: "{}" });
    assert.equal(response.status, 405, method);
  }
});

test("плашка «Бренды и коды» показывается по тому же праву", async () => {
  const env = envBrands();
  assert.match(await (await call(env, "/", READER)).text(), /href="\/brands"/);
  assert.doesNotMatch(await (await call(env, "/", GUEST)).text(), /href="\/brands"/);
});

// ЖИВОЙ ПОИСК ПО СПРОСУ. Одна фиксированная функция базы: клиент задаёт только
// строку поиска. Сеть подменена — проверяется, ЧТО воркер отправляет и что
// пропускает обратно.
test("поиск по спросу: право, проверка строки, одна фиксированная функция", async () => {
  const env = envBrands();
  env.SUPABASE_SERVICE_KEY = "sb_secret_TESTKEYTESTKEY";
  const сеть = globalThis.fetch;
  const вызовы = [];
  globalThis.fetch = async (u, init) => {
    вызовы.push({ u: String(u), init });
    return new Response(JSON.stringify({ q: "x", key: "ab6205",
      by_code: [{ code: "ab6205", written: "AB-6205", name: "Подшипник учебный", rows: 3, deals: 2, deal_id: "D-1" }],
      by_words: [], word_rows: 0, capped: false }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const deny = await call(env, "/api/brands/search?q=AB-6205", GUEST);
    assert.equal(deny.status, 403);
    for (const q of ["", "a", "x".repeat(81), "ab\u0001cd"]) {
      const bad = await call(env, "/api/brands/search?q=" + encodeURIComponent(q), READER);
      assert.equal(bad.status, 400, JSON.stringify(q));
    }
    assert.equal(вызовы.length, 0, "отказ не должен ходить в базу");
    const ok = await call(env, "/api/brands/search?q=" + encodeURIComponent("AB-6205") + "&fn=drop", READER);
    assert.equal(ok.status, 200);
    const v = await ok.json();
    assert.equal(v.by_code[0].code, "ab6205");
    assert.equal(v.by_code[0].deals, 2);
    // Лишнее поле ответа базы (номер сделки) дальше воркера не идёт.
    assert.equal(v.by_code[0].deal_id, undefined);
    assert.equal(вызовы.length, 1);
    assert.match(вызовы[0].u, /\/rest\/v1\/rpc\/lib_code_search$/);
    assert.deepEqual(JSON.parse(вызовы[0].init.body), { q: "AB-6205", lim: 20 });
    assert.equal(вызовы[0].init.headers.apikey, "sb_secret_TESTKEYTESTKEY");
  } finally { globalThis.fetch = сеть; }
});

test("поиск по спросу без ключа базы — 503 с причиной, а не пустота", async () => {
  const env = envBrands();
  const response = await call(env, "/api/brands/search?q=AB-6205", READER);
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error, "search_key_missing");
});
