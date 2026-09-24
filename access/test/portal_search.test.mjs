// Единый поиск портала: маршрут /api/portal/search и место поиска на стартовой
// странице. Гоняется настоящий public/_worker.js; сеть подменена — проверяется,
// ЧТО воркер отправляет в базу и что пропускает обратно.
//
// Синтетические данные: ни строки из CRM, ни производственных ключей.
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../../public/_worker.js";
import { defaultAcl } from "../acl.js";

const ORIGIN = "https://portal.example.test";
const TEAM = "portal-search-test.cloudflareaccess.com";
const OWNER = "owner@example.test";
const READER = "reader@example.test";     // право suppliers, библиотеки нет
const ZIPPER = "zipper@example.test";     // сайт ГШО, права suppliers нет
const GUEST = "guest@example.test";
const encoder = new TextEncoder();
const b64 = (v) => Buffer.from(v).toString("base64url");
const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "portal-search-key", alg: "RS256" };
async function token(email) {
  const input = b64(JSON.stringify({ alg: "RS256", kid: jwk.kid })) + "." +
    b64(JSON.stringify({ email, iss: `https://${TEAM}`, exp: Math.floor(Date.now() / 1000) + 600 }));
  return input + "." + b64(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, encoder.encode(input)));
}

function envFor(extra = {}) {
  const box = new Map();
  const assets = [];
  const acl = defaultAcl();
  acl.defaultRole = "guest";
  acl.users[READER] = { role: "guest", sites: [], tabs: [], rights: ["suppliers"] };
  acl.users[ZIPPER] = { role: "guest", sites: ["zip"], tabs: [], rights: [] };
  box.set("acl:v1", JSON.stringify(acl));
  return { CF_ACCESS_TEAM: TEAM, __certs: { keys: [jwk] }, ADMIN_EMAILS: OWNER, assets,
    ACL: { box, get: async (key, options) => {
      const raw = box.get(key) ?? null;
      return raw !== null && options?.type === "json" ? JSON.parse(raw) : raw;
    }, put: async (key, value) => { box.set(key, value); },
      list: async ({ prefix = "" }) => ({ keys: [...box.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
        list_complete: true }) },
    ASSETS: { fetch: async (request) => {
      const path = new URL(request.url).pathname;
      assets.push(path);
      if (path === "/portal_search.js") {
        return new Response("/* поиск */", { headers: { "Content-Type": "application/javascript" } });
      }
      return new Response("<!doctype html><title>fixture</title><main>UI SHELL</main>",
        { headers: { "Content-Type": "text/html" } });
    } },
    ...extra,
  };
}
const ctx = { waitUntil: (promise) => Promise.resolve(promise).catch(() => {}) };
async function call(env, path, email = READER, init = {}) {
  const headers = new Headers(init.headers);
  if (email) headers.set("Cf-Access-Jwt-Assertion", await token(email));
  return worker.fetch(new Request(ORIGIN + path, { ...init, headers }), env, ctx);
}

// Ответ базы — как его отдаёт PostgREST для функции, возвращающей таблицу:
// массив строк. Намеренно с мусором: чужой вид, лишние поля, почта в счётчиках.
const ОТВЕТ = [
  { kind: "поставщик", key: "KV-S-000011-1", title: "Альфа-Подшипник", subtitle: "KV-S-000011-1 · ИНН 7700000001",
    brand: null, brand_key: null, brand_src: null, segment: null,
    counts: { offers: 3, codes: 2, emails: ["nobody@example.test"] }, source: "ИНН", rank: 0,
    deal_id: "D-1" },
  { kind: "код", key: "ab6205", title: "AB-6205", subtitle: "Подшипник выдуманный", brand: "SKF",
    brand_key: "skf", brand_src: "частота", segment: null,
    counts: { deals: 2, offers: 1, capped: true, deals_list: [1, 2] }, source: "спрос · КП", rank: 1 },
  { kind: "код", key: "ab6205zz", title: "AB-6205ZZ", subtitle: null, brand: "Выдуманный литейщик",
    brand_key: null, brand_src: "написание", segment: null, counts: { deals: 1 }, source: "спрос", rank: 1 },
  { kind: "машина", key: "vm400", title: "ВМ-400", subtitle: "турбина", brand: null, brand_key: null,
    brand_src: null, segment: "gtu<script>", counts: { parts: 2, fleet: 1 }, source: "справочник машин", rank: 2 },
  { kind: "сделка", key: "D-1", title: "Не наш вид", rank: 0 },
  { kind: "усечено", key: "узел", rank: 99 },
];

async function сетью(ответ, fn, status = 200) {
  const сеть = globalThis.fetch;
  const вызовы = [];
  globalThis.fetch = async (u, init) => {
    вызовы.push({ u: String(u), init });
    return new Response(typeof ответ === "string" ? ответ : JSON.stringify(ответ),
      { status, headers: { "Content-Type": "application/json" } });
  };
  try { return await fn(вызовы); } finally { globalThis.fetch = сеть; }
}

test("поиск портала: право, проверка строки, одна фиксированная функция", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  await сетью(ОТВЕТ, async (вызовы) => {
    assert.equal((await call(env, "/api/portal/search?q=AB-6205", GUEST)).status, 403);
    assert.equal((await call(env, "/api/portal/search?q=AB-6205", ZIPPER)).status, 403);
    for (const q of ["", "a", " b ", "x".repeat(81), "ab\u0001cd"]) {
      const bad = await call(env, "/api/portal/search?q=" + encodeURIComponent(q), READER);
      assert.equal(bad.status, 400, JSON.stringify(q));
    }
    assert.equal(вызовы.length, 0, "отказ не должен ходить в базу");
    const ok = await call(env, "/api/portal/search?q=" + encodeURIComponent("AB-6205") + "&fn=drop&lim=500", READER);
    assert.equal(ok.status, 200);
    assert.equal(ok.headers.get("Cache-Control").includes("no-store"), true);
    assert.equal(вызовы.length, 1);
    assert.match(вызовы[0].u, /\/rest\/v1\/rpc\/portal_search$/);
    // Клиент задаёт только строку: ни имени функции, ни предела.
    assert.deepEqual(JSON.parse(вызовы[0].init.body), { q: "AB-6205", lim: 8 });
    assert.equal(вызовы[0].init.headers.apikey, "sb_secret_TESTKEYTESTKEY");
    assert.equal(вызовы[0].init.headers.Authorization, undefined);
  });
});

test("ответ базы идёт белым списком: чужой вид, лишние поля и счётчики отсекаются", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  await сетью(ОТВЕТ, async () => {
    const text = await (await call(env, "/api/portal/search?q=AB-6205", READER)).text();
    for (const secret of ["nobody@example.test", "D-1", "deals_list", "Не наш вид"]) {
      assert.ok(!text.includes(secret), `в ответе осталось «${secret}»`);
    }
    const v = JSON.parse(text);
    assert.equal(v.q, "AB-6205");
    assert.deepEqual(v.rows.map((r) => r.kind), ["поставщик", "код", "код", "машина"]);
    const код = v.rows[1];
    assert.deepEqual(код.counts, { deals: 2, offers: 1, capped: true });
    assert.equal(код.brand, "SKF");
    assert.equal(код.brand_key, "skf");
    // Сегмент с посторонними знаками в ссылку не идёт.
    assert.equal(v.rows[3].segment, null);
    // «Не успели» — отдельным полем, а не строкой выдачи.
    assert.deepEqual(v.partial, ["узел"]);
    assert.equal(v.library, false, "у читателя нет права на библиотеку");
    const owner = await (await call(env, "/api/portal/search?q=AB-6205", OWNER)).json();
    assert.equal(owner.library, true);
  });
});

test("виды встают по лучшему рангу, внутри ранга — порядок базы", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  const ответ = [
    { kind: "код", key: "k2", title: "K2", counts: {}, rank: 1 },
    { kind: "код", key: "k1", title: "K1", counts: {}, rank: 1 },
    { kind: "бренд", key: "b0", title: "B0", counts: {}, rank: 0 },
    { kind: "код", key: "k0", title: "K0", counts: {}, rank: 0 },
    { kind: "узел", key: "u0", title: "U0", counts: {}, rank: 0 },
  ];
  await сетью(ответ, async () => {
    const v = await (await call(env, "/api/portal/search?q=kk", READER)).json();
    assert.deepEqual(v.rows.map((r) => r.key), ["k0", "b0", "u0", "k2", "k1"]);
  });
});

test("поиск портала без ключа, без функции и с чужим ответом — 503 с причиной", async () => {
  const без = await call(envFor(), "/api/portal/search?q=AB-6205", READER);
  assert.equal(без.status, 503);
  assert.equal((await без.json()).error, "search_key_missing");
  const env = envFor({ SUPABASE_SERVICE_KEY: "legacy-service-role-jwt" });
  await сетью({ code: "PGRST202" }, async (вызовы) => {
    const r = await call(env, "/api/portal/search?q=AB-6205", READER);
    assert.equal(r.status, 503);
    assert.equal((await r.json()).error, "search_not_installed");
    // Ключ старого вида идёт и заголовком Authorization, как у поиска по спросу.
    assert.equal(вызовы[0].init.headers.Authorization, "Bearer legacy-service-role-jwt");
  }, 404);
  await сетью({ rows: [] }, async () => {
    const r = await call(env, "/api/portal/search?q=AB-6205", READER);
    assert.equal(r.status, 503);
    assert.equal((await r.json()).error, "search_unavailable");
  });
  await сетью("x".repeat(300 * 1024), async () => {
    const r = await call(env, "/api/portal/search?q=AB-6205", READER);
    assert.equal(r.status, 503);
  });
});

test("альтернативные написания пути и чужие методы не открывают поиск", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  for (const path of ["/api/portal/search/x", "/api/portal/search;x", "/api/portal%2fsearch",
                      "/api/portal", "/api/%70ortal/search", "/api/portal/other"]) {
    const response = await call(env, path + "?q=AB-6205", GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  const post = await call(env, "/api/portal/search?q=AB-6205", READER,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  assert.equal(post.status, 405);
  assert.deepEqual(env.assets, []);
});

test("стартовая страница: место поиска над плитками — только с правом suppliers", async () => {
  const env = envFor();
  const reader = await (await call(env, "/", READER)).text();
  assert.match(reader, /<div id="kvps" class="kvps-page"><\/div><script src="\/portal_search\.js" defer><\/script>/);
  // Место — выше плиток, плитки на месте.
  assert.ok(reader.indexOf('id="kvps"') < reader.indexOf('class="tile"'), "поиск должен стоять над плитками");
  for (const href of ["/suppliers", "/nomenclature", "/brands", "/counters"]) {
    assert.ok(reader.includes(`href="${href}"`), href);
  }
  const zipper = await (await call(env, "/", ZIPPER)).text();
  assert.doesNotMatch(zipper, /id="kvps"|portal_search\.js/);
  assert.ok(zipper.includes("ГШО — горно-шахтное оборудование"), "плитки без права suppliers пропали");
  assert.doesNotMatch(await (await call(env, "/", GUEST)).text(), /id="kvps"/);
  assert.match(await (await call(env, "/", OWNER)).text(), /id="kvps"/);
});

test("скрипт поиска отдаётся как статика и ни одним маршрутом не перехватывается", async () => {
  const env = envFor();
  const r = await call(env, "/portal_search.js", READER);
  assert.equal(r.status, 200);
  assert.match(r.headers.get("Content-Type"), /javascript/);
  assert.deepEqual(env.assets, ["/portal_search.js"]);
});
