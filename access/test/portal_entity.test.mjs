// Карточки портала: маршруты /api/portal/code, /api/portal/brand,
// /api/portal/supplier и страница /p. Гоняется настоящий public/_worker.js; сеть
// подменена — проверяется, ЧТО воркер отправляет в базу и что пропускает обратно.
//
// Что проверяется по существу:
//   · право suppliers — и на данные, и на страницу; без права 403, и в базу
//     воркер не ходит;
//   · клиент задаёт только ключ: функция и имя её параметра — воркера;
//   · ответ — закрытым списком полей: контакт, почта, номер сделки, лишнее
//     поле базы и значение не того вида дальше воркера не идут; условия КП
//     (оплата, сроки) — часть предложения и идут, как на /nomenclature; номер
//     карточки Битрикса — только цифрами и только своим полем для ссылки;
//   · ни одно поле схем карточек не совпадает с полем, которое режет право
//     (SUPPLIERS_FIELDS), — а совпади оно, карточку режет suppliersCut;
//   · «нет такого», «не код», «реестра нет», «функции нет» — разными ответами;
//   · прочие пути под /p и /api/portal — 404, а не страница из ASSETS;
//   · прежние страницы раздела по-прежнему отдаются.
//
// Синтетические данные: ни строки из CRM, ни производственных ключей.
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../../public/_worker.js";
import { defaultAcl } from "../acl.js";

const ORIGIN = "https://portal.example.test";
const TEAM = "portal-entity-test.cloudflareaccess.com";
const OWNER = "owner@example.test";
const READER = "reader@example.test";     // право suppliers, библиотеки нет
const ZIPPER = "zipper@example.test";     // сайт ГШО, права suppliers нет
const GUEST = "guest@example.test";
const encoder = new TextEncoder();
const b64 = (v) => Buffer.from(v).toString("base64url");
const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "portal-entity-key", alg: "RS256" };
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
      if (path.endsWith(".js")) return new Response("/* скрипт */", { headers: { "Content-Type": "application/javascript" } });
      return new Response(`<!doctype html><title>${path}</title><main>UI SHELL</main>`,
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

async function сетью(ответ, fn, status = 200) {
  const сеть = globalThis.fetch;
  const вызовы = [];
  globalThis.fetch = async (u, init) => {
    вызовы.push({ u: String(u), init, body: init?.body ? JSON.parse(init.body) : null });
    const v = typeof ответ === "function" ? ответ(String(u), init) : ответ;
    return new Response(typeof v === "string" ? v : JSON.stringify(v),
      { status, headers: { "Content-Type": "application/json" } });
  };
  try { return await fn(вызовы); } finally { globalThis.fetch = сеть; }
}

// Ответ базы на карточку кода — намеренно с мусором: почта, телефон, номер
// сделки и карточки портала, лишние поля, ключ бренда не того вида.
const КОД = {
  key: "kl7", written: "KL-7", name: "Клапан выдуманный", kv_no: null, catalog: true,
  brand: { key: "kelton", name: "Kelton GmbH", src: "каталог", disputed: false, sp176: 50501 },
  brands: [{ key: "kelton", name: "Kelton GmbH", rows: 5, sources: ["каталог", "КП"], best: 0 }],
  demand: { rows: 1, deals: 1, units: 1, qty: 1, unit: "шт", qty_hidden: 0, last_month: "2026-09",
            customers: null, capped: false, deal_ids: ["D-1"] },
  offers: {
    rows: 2, suppliers: 2, cards: 2, capped: false, brand_judged: true, original_n: 1, analog_n: 1,
    original: [{ company: { id: "KV-S-000011-1", name: "Альфа-Подшипник", src: "bitrix:title", number: null,
                            emails: ["nobody@example.test"], phone: "+0 000" },
                 brand: { key: "kelton", name: "Kelton GmbH" }, written: "KL-7", price: 100, currency: "EUR",
                 qty: 2, qty_hidden: false, unit: null, total: 200, basis: "DDP", lead_days: null,
                 month: "2026-03", month_src: "документ", rfq_company: "91101", pay_terms: "LC 30/70",
                 pay_src: "строка", lead_src: "выдумано", rfq: "4401", note: "позвонить Ивану" }],
    analog: [{ company: null, bx: "91301", unresolved: true, brand: null, written: "KL-7", price: 90, currency: "EUR",
               qty: "много", qty_hidden: false, month: "март", why: "поставщик пишет «аналог»", rfq: "R5" },
             { company: null, bx: "91 301<b>", unresolved: true, price: 1, currency: "EUR" }],
  },
  analogs: [{ code: "an4004", written: "AN-4004", kind: "аналог", brand: { key: null, name: "Выдуманный литейщик" } },
            { code: "SS 316!", written: "SS316", kind: "аналог", brand: { key: "Не Ключ", name: "X" } }],
  analog_of: [],
  machines: [{ id: "vm400", name: "ВМ-400", kind: "турбина", segment: "gtu<script>", brand: { key: "kelton", name: "Kelton GmbH" } }],
  units: [{ id: "hot.liner", name: "Жаровая труба", parent: "Горячая часть", crit: "A" }],
  write_to: [], registry: true, partial: [], deal_id: "D-1", contacts: ["nobody@example.test"],
};

test("карточки: право suppliers, проверка ключа, одна фиксированная функция на карточку", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  await сетью(КОД, async (вызовы) => {
    for (const path of ["/api/portal/code?k=kl7", "/api/portal/brand?b=kelton",
                        "/api/portal/supplier?s=KV-S-000011-1", "/p", "/portal_entity.html"]) {
      assert.equal((await call(env, path, GUEST)).status, 403, path);
      assert.equal((await call(env, path, ZIPPER)).status, 403, path);
    }
    for (const path of ["/api/portal/code?k=", "/api/portal/code?k=" + "x".repeat(121), "/api/portal/code?k=a%01b",
                        "/api/portal/brand?b=Kelton", "/api/portal/brand?b=", "/api/portal/brand?b=kel%20ton",
                        "/api/portal/supplier?s=KV-S-1", "/api/portal/supplier?s=91101", "/api/portal/supplier?s=",
                        // Не-ASCII буква, которую toUpperCase() свёл бы к «S» (ſ), и знак кельвина вместо «K».
                        "/api/portal/supplier?s=kv-%C5%BF-000011-1", "/api/portal/supplier?s=%E2%84%AAV-S-000011-1"]) {
      const bad = await call(env, path, READER);
      assert.equal(bad.status, 400, path);
      assert.equal((await bad.json()).error, "invalid_key", path);
    }
    assert.equal(вызовы.length, 0, "отказ не должен ходить в базу");
    for (const [path, fn, body] of [
      ["/api/portal/code?k=" + encodeURIComponent("KL-7") + "&fn=drop", "portal_code", { key: "KL-7" }],
      ["/api/portal/brand?b=kelton&lim=999", "portal_brand", { brand_key: "kelton" }],
      ["/api/portal/supplier?s=kv-s-000011-1", "portal_supplier", { sup_id: "kv-s-000011-1" }],
    ]) {
      const ok = await call(env, path, READER);
      assert.equal(ok.status, 200, path);
      assert.ok(ok.headers.get("Cache-Control").includes("no-store"));
      const последний = вызовы[вызовы.length - 1];
      assert.match(последний.u, new RegExp("/rest/v1/rpc/" + fn + "$"));
      assert.deepEqual(последний.body, body, path);
      assert.equal(последний.init.headers.apikey, "sb_secret_TESTKEYTESTKEY");
      assert.equal(последний.init.headers.Authorization, undefined);
    }
  });
});

test("карточка кода идёт закрытым списком: контакты, деньги сделки и лишние поля отсекаются", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  await сетью(КОД, async () => {
    const text = await (await call(env, "/api/portal/code?k=kl7", READER)).text();
    for (const secret of ["nobody@example.test", "+0 000", "D-1", "deal_ids", "91101", "rfq_company",
                          "позвонить", "50501", "contacts", "\"best\"", "<script>", "<b>", "выдумано"]) {
      assert.ok(!text.includes(secret), `в ответе осталось «${secret}»`);
    }
    const v = JSON.parse(text);
    assert.equal(v.written, "KL-7");
    assert.deepEqual(v.brand, { key: "kelton", name: "Kelton GmbH", src: "каталог", disputed: false });
    assert.deepEqual(Object.keys(v.offers.original[0].company), ["id", "name", "src", "number"]);
    // Значение не того вида — null, а не строка: «много» не количество, «март» не месяц.
    assert.equal(v.offers.analog[0].qty, null);
    assert.equal(v.offers.analog[0].month, null);
    assert.equal(v.offers.analog[0].why, "поставщик пишет «аналог»");
    // Условия КП — часть предложения, как цена; источник условия — только
    // словом из закрытого списка.
    assert.equal(v.offers.original[0].pay_terms, "LC 30/70");
    assert.equal(v.offers.original[0].pay_src, "строка");
    assert.equal(v.offers.original[0].lead_src, null);
    // Номер карточки Битрикса (компании и запроса) — только цифрами.
    assert.equal(v.offers.original[0].rfq, "4401");
    assert.equal(v.offers.analog[0].rfq, null);
    assert.equal(v.offers.analog[0].bx, "91301");
    assert.equal(v.offers.analog[1].bx, null);
    // Ключ кода и бренда — только ключ: «SS 316!» ссылкой не станет.
    assert.equal(v.analogs[1].code, null);
    assert.equal(v.analogs[1].brand.key, null);
    assert.equal(v.machines[0].segment, null);
    assert.equal(v.library, false, "у читателя нет права на библиотеку");
    const owner = await (await call(env, "/api/portal/code?k=kl7", OWNER)).json();
    assert.equal(owner.library, true);
  });
});

test("карточки бренда и поставщика — тоже закрытым списком", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  const бренд = { key: "kelton", name: "Kelton GmbH", country: "Нигдения", owner: null, former_names: null,
    spellings: ["Келтон"], demand: { rows: 3, deals: 3, codes: 2, capped: false, registry_rows: 3 },
    codes_demand: [{ code: "qx1001", written: "QX 1001", deals: 2, rows: 2, deal_ids: ["D-9"] }],
    codes_offers: [], offers: { rows: 7, capped: false, suppliers: 2, rows_unresolved: 1 },
    catalog: { parts: 1, list: [{ code: "kl7", written: "KL-7", name: "Клапан", kv_no: null }] },
    machines: [], suppliers: [{ company: { id: "KV-S-000012-2", name: "Бета", src: "написание", number: "KV-S-000012-2",
      persons: ["Иван"] }, codes: 3, rows: 3, last_month: "2026-05", terms: "аванс 100 %" }],
    analogs: [], registry: true, partial: [] };
  const поставщик = { id: "KV-S-000012-2", merged_from: null, name: "Бета", name_src: "написание",
    number: "KV-S-000012-2", inn: ["7700000012", "не ИНН"], domains: ["beta.example"], country: "Нигдения",
    city: null, status: "active", bitrix: ["91201", "x1"], rfq: { sent: 5, answered: 3, quoted: 2, silent: 1,
      no_outcome: 1, cards: 6, who: "nobody@example.test" },
    quotes: { rows: 4, cards: 4, codes: 4, last_month: "2026-05", capped: false },
    brands: [], codes: [{ code: "kl7", written: "KL-7", brand: null, price: 95, currency: "EUR", qty: null,
      unit: null, month: "2026-02", offers: 1, pay_terms: "LC" }],
    contacts: [{ email: "nobody@example.test" }], phones: ["+0 000"], payment: "30/70", registry: true, partial: [] };
  await сетью((u) => (u.includes("portal_brand") ? бренд : поставщик), async () => {
    const tb = await (await call(env, "/api/portal/brand?b=kelton", READER)).text();
    for (const secret of ["D-9", "Иван", "аванс", "persons", "terms"]) assert.ok(!tb.includes(secret), secret);
    assert.equal(JSON.parse(tb).suppliers[0].company.name, "Бета");
    const ts = await (await call(env, "/api/portal/supplier?s=KV-S-000012-2", READER)).text();
    for (const secret of ["nobody@example.test", "+0 000", "30/70", "contacts", "phones", "payment", "pay_terms",
                          "не ИНН", "x1"]) {
      assert.ok(!ts.includes(secret), secret);
    }
    const s = JSON.parse(ts);
    assert.deepEqual(s.inn, ["7700000012"]);
    assert.deepEqual(s.bitrix, ["91201"]);
    assert.deepEqual(s.rfq, { sent: 5, answered: 3, quoted: 2, silent: 1, no_outcome: 1, cards: 6 });
  });
});

test("поля схем карточек не совпадают с полями, которые режет право", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../public/_worker.js", import.meta.url), "utf8");
  const режет = src.slice(src.indexOf("const SUPPLIERS_FIELDS"), src.indexOf("function suppliersCut"));
  const закрытые = new Set([...режет.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).filter((x) => !x.startsWith("suppliers")));
  assert.ok(закрытые.has("contacts") && закрытые.has("payment"), [...закрытые].join(","));
  const схемы = src.slice(src.indexOf("const ПЕ_КОД"), src.indexOf("const PORTAL_ENTITIES"));
  const поля = new Set([...схемы.matchAll(/([a-z_]+):\s*(?:ПС|ПЕ_|\{|\.\.\.)/g)].map((m) => m[1]));
  assert.ok(поля.has("pay_terms") && поля.has("company"), [...поля].join(","));
  for (const поле of поля) assert.ok(!закрытые.has(поле), `поле «${поле}» режется правом — нужна отдельная обработка`);
});

test("не карточка — словами: нет такого, не код, реестра нет, функции нет, база молчит", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "legacy-service-role-jwt" });
  await сетью(null, async () => {
    const r = await call(env, "/api/portal/code?k=nope", READER);
    assert.equal(r.status, 404);
    assert.equal((await r.json()).error, "not_found");
  });
  await сетью({ key: "ss316", rejected: true }, async () => {
    const r = await call(env, "/api/portal/code?k=SS316", READER);
    assert.equal(r.status, 404);
    assert.deepEqual(await r.json(), { error: "not_a_code", key: "ss316" });
  });
  await сетью({ registry: false }, async () => {
    const r = await call(env, "/api/portal/brand?b=kelton", READER);
    assert.equal(r.status, 503);
    assert.equal((await r.json()).error, "brands_not_installed");
  });
  await сетью({ code: "PGRST202" }, async (вызовы) => {
    const r = await call(env, "/api/portal/supplier?s=KV-S-000011-1", READER);
    assert.equal(r.status, 503);
    assert.equal((await r.json()).error, "entity_not_installed");
    // Ключ старого вида идёт и заголовком Authorization, как у поиска.
    assert.equal(вызовы[0].init.headers.Authorization, "Bearer legacy-service-role-jwt");
  }, 404);
  await сетью([1, 2], async () => {
    assert.equal((await call(env, "/api/portal/code?k=kl7", READER)).status, 503);
  });
  await сетью("x".repeat(600 * 1024), async () => {
    assert.equal((await call(env, "/api/portal/code?k=kl7", READER)).status, 503);
  });
  const без = await call(envFor(), "/api/portal/code?k=kl7", READER);
  assert.equal(без.status, 503);
  assert.equal((await без.json()).error, "search_key_missing");
});

test("страница /p — под правом, с той же защитой, что у страниц раздела", async () => {
  const env = envFor();
  for (const path of ["/p", "/p/", "/portal_entity", "/portal_entity.html"]) {
    const r = await call(env, path, READER);
    assert.equal(r.status, 200, path);
    assert.match(r.headers.get("Content-Type"), /text\/html/);
    assert.match(r.headers.get("Content-Security-Policy"), /script-src 'self'/);
    assert.ok(r.headers.get("Cache-Control").includes("no-store"));
  }
  assert.deepEqual([...new Set(env.assets)], ["/portal_entity.html"]);
  // Скрипт страницы — статика, как у поиска.
  const js = await call(env, "/portal_entity.js", READER);
  assert.equal(js.status, 200);
  assert.match(js.headers.get("Content-Type"), /javascript/);
});

test("прочие пути под /p и /api/portal — 404, а не страница из ASSETS", async () => {
  const env = envFor({ SUPABASE_SERVICE_KEY: "sb_secret_TESTKEYTESTKEY" });
  for (const path of ["/p/x", "/p;x", "/p.html", "/%70", "/p%2fx", "/portal_entity.htm", "/portal_entity/x",
                      "/api/portal/code/x", "/api/portal/code;x", "/api/portal%2fcode", "/api/portal/brands",
                      "/api/portal/supplier/", "/api/portal/other"]) {
    const response = await call(env, path + "?k=kl7&b=kelton&s=KV-S-000011-1", GUEST);
    assert.equal(response.status, 404, path);
    assert.equal((await response.json()).error, "not_found");
  }
  const post = await call(env, "/api/portal/code?k=kl7", READER,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  assert.equal(post.status, 405);
  assert.deepEqual(env.assets, []);
});

test("прежние страницы раздела и их адреса работают как раньше", async () => {
  const env = envFor();
  for (const [path, файл] of [["/nomenclature", "/nomenclature.html"], ["/brands", "/brands.html"],
                              ["/suppliers", "/suppliers.html"], ["/counters", "/counters.html"]]) {
    env.assets.length = 0;
    const r = await call(env, path, READER);
    assert.equal(r.status, 200, path);
    assert.deepEqual(env.assets, [файл], path);
  }
  // Путь, начинающийся с «p», но не страница карточек, в раздел не попадает.
  env.assets.length = 0;
  const поиск = await call(env, "/portal_search.js", READER);
  assert.equal(поиск.status, 200);
  assert.deepEqual(env.assets, ["/portal_search.js"]);
  const портал = await (await call(env, "/", READER)).text();
  assert.match(портал, /id="kvps"/);
});
