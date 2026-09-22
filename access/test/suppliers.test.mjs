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
