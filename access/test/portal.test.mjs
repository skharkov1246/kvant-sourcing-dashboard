// Сквозная проверка гейта портала: вход, портал, дашборд по правам, админка.
// Гоняем настоящий public/_worker.js — тот же файл, что уезжает в Cloudflare.
// Запуск: node --test access/test/portal.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import worker from "../../public/_worker.js";
import { PAYLOADS, defaultAcl, loadAcl, loadSeen } from "../acl.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const TEAM = "test-team.cloudflareaccess.com";

// ── подпись входа, как её ставит Cloudflare Access ───────────────────────────
const b64u = (b) => Buffer.from(b).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
let priv, jwk;
async function keys() {
  if (priv) return;
  const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  priv = kp.privateKey;
  jwk = { ...(await crypto.subtle.exportKey("jwk", kp.publicKey)), kid: "k1", use: "sig", alg: "RS256" };
}
async function jwtFor(email) {
  await keys();
  const now = Math.floor(Date.now() / 1000);
  const h = b64u(JSON.stringify({ alg: "RS256", kid: "k1" }));
  const p = b64u(JSON.stringify({ email, sub: email, iss: `https://${TEAM}`, exp: now + 600, iat: now }));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", priv, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}

// ── подпорки Cloudflare: KV на карте, ASSETS на боевом шаблоне ───────────────
function kv() {
  const box = new Map();
  return {
    box,
    get: async (k, o) => { const v = box.get(k); return v == null ? null : (o && o.type === "json" ? JSON.parse(v) : v); },
    put: async (k, v) => { box.set(k, v); },
    list: async ({ prefix = "" } = {}) => ({
      keys: [...box.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
      list_complete: true,
    }),
  };
}
function dashboardHtml() {
  let html = fs.readFileSync(path.join(ROOT, "templates/dashboard_core.html"), "utf8");
  for (const p of PAYLOADS) html = html.replace(`__${p}_JSON__`, `["MOCK-${p}"]`);
  return html.replace(/__[A-Z_]+_JSON__/g, "null");
}
const ASSETS = { fetch: async () => new Response(dashboardHtml(), { headers: { "Content-Type": "text/html; charset=utf-8" } }) };
const CTX = { waitUntil: (p) => Promise.resolve(p).catch(() => {}) };

function makeEnv(extra = {}) {
  return { CF_ACCESS_TEAM: TEAM, __certs: { keys: [jwk] }, ACL: kv(), ASSETS, ADMIN_EMAILS: "boss@kvantpro.com", ...extra };
}
async function call(env, url, email, init = {}) {
  const headers = new Headers(init.headers || {});
  if (email) headers.set("Cf-Access-Jwt-Assertion", await jwtFor(email));
  return worker.fetch(new Request("https://kvant-sourcing-f122.pages.dev" + url, { ...init, headers }), env, CTX);
}
async function seed(env, users, patch = {}) {
  const acl = { ...defaultAcl(), ...patch };
  acl.users = users;
  await env.ACL.put("acl:v1", JSON.stringify(acl));
}

test("без подписи входа не отдаётся ничего", async () => {
  await keys();
  const env = makeEnv();
  for (const p of ["/", "/dashboard", "/admin", "/api/rights"]) {
    const r = await call(env, p, null);
    assert.equal(r.status, 403, p);
    assert.ok(!(await r.text()).includes("MOCK-"), `${p}: данные утекли неавторизованному`);
  }
});

test("портал показывает только выданные человеку сайты", async () => {
  const env = makeEnv();
  await seed(env, { "s@kvantpro.com": { role: "sourcing", sites: [], tabs: [], note: "", seen: 1 } });
  const html = await (await call(env, "/", "s@kvantpro.com")).text();
  assert.ok(html.includes("Дашборд сорсинга") && html.includes("ГПУ — газопоршневые установки"));
  assert.ok(html.includes("ГШО — горно-шахтное оборудование") && html.includes("ГТУ — газотурбинные установки"));
  assert.ok(!html.includes("Гидрометаллургия"), "показан невыданный сайт");
  assert.ok(html.includes("Библиотеки оборудования"), "нет плашки раздела");
  assert.ok(!html.includes("Спецпроекты"), "пустая плашка не должна показываться");
  assert.ok(!html.includes('href="/admin"'), "рядовому сотруднику видна ссылка на админку");

  const none = makeEnv();
  await seed(none, { "g@kvantpro.com": { role: "guest", sites: [], tabs: [], note: "", seen: 1 } });
  assert.match(await (await call(none, "/", "g@kvantpro.com")).text(), /Доступ к разделам пока не выдан/);
});

test("вход учитывается на любой странице, а не только на корне портала", async () => {
  // именно этим был сломан учёт: кто заходил по закладке сразу на дашборд, в список не попадал
  for (const p of ["/", "/dashboard", "/api/rights"]) {
    const env = makeEnv();
    await call(env, p, "new@kvantpro.com");
    assert.ok((await loadSeen(env))["new@kvantpro.com"], `вход через ${p} не учтён`);
  }
  // служебные запросы не считаем
  const env = makeEnv();
  await call(env, "/fonts/x.woff2", "new@kvantpro.com");
  assert.deepEqual(await loadSeen(env), {}, "запрос шрифта не должен считаться входом");
});

test("вошедший виден в панели, даже если прав ему ещё не назначали", async () => {
  const env = makeEnv();
  await seed(env, {});
  await call(env, "/dashboard", "nov@kvantpro.com");
  const html = await (await call(env, "/admin", "boss@kvantpro.com")).text();
  assert.ok(html.includes("nov@kvantpro.com"), "вошедшего нет в списке панели");
  assert.ok(!html.includes("Учёт входов не ведётся"), "ложная тревога при работающем хранилище");
});

test("без хранилища панель говорит об этом прямо, а не показывает пустой список", async () => {
  const env = makeEnv({ ACL: undefined, VISITS: undefined });
  const html = await (await call(env, "/admin", "boss@kvantpro.com")).text();
  assert.match(html, /Учёт входов не ведётся/);
  assert.match(html, /KV namespace/, "не сказано, что именно сделать");
});

test("роль назначается вошедшему, у которого строки прав ещё нет", async () => {
  const env = makeEnv();
  await seed(env, {});
  await call(env, "/", "nov@kvantpro.com");
  const r = await call(env, "/admin/api", "boss@kvantpro.com",
    { method: "POST", body: JSON.stringify({ op: "user_role", email: "nov@kvantpro.com", role: "kam" }) });
  assert.equal((await r.json()).ok, true);
  assert.equal((await loadAcl(env)).users["nov@kvantpro.com"].role, "kam");
});

test("дашборд режется по правам: чужих данных в исходном коде нет", async () => {
  const env = makeEnv();
  await seed(env, { "k@kvantpro.com": { role: "kam", sites: [], tabs: [], note: "", seen: 1 } });
  const html = await (await call(env, "/dashboard", "k@kvantpro.com")).text();
  assert.ok(html.includes("MOCK-KAM") && html.includes("MOCK-COMPANY"), "своих данных нет");
  for (const p of ["ENG", "PRODUCT", "ADVISOR", "CONTRACTS", "DATA", "INSIGHTS"]) {
    assert.ok(!html.includes(`MOCK-${p}`), `данные ${p} утекли КАМу`);
  }
  assert.doesNotMatch(html, /data-tab="advisor"/);
  assert.ok(html.includes("← Портал КВАНТ"), "нет возврата на портал");
});

test("сайт без права не открывается даже по прямой ссылке", async () => {
  const env = makeEnv();
  await seed(env, { "e@kvantpro.com": { role: "engineer", sites: [], tabs: [], note: "", seen: 1 } });
  const r = await call(env, "/dashboard", "e@kvantpro.com");
  assert.equal(r.status, 403);
  assert.ok(!(await r.text()).includes("MOCK-"));
});

test("/api/rights отвечает правами вошедшего — для гейтов остальных сайтов", async () => {
  const env = makeEnv();
  await seed(env, { "s@kvantpro.com": { role: "sourcing", sites: [], tabs: [], note: "", seen: 1 } });
  const d = await (await call(env, "/api/rights", "s@kvantpro.com")).json();
  assert.deepEqual(d.sites, ["dashboard", "zip", "gt", "gpu"]);
  assert.equal(d.admin, false);
  assert.equal(d.email, "s@kvantpro.com");
});

test("админка закрыта для всех, кроме владельца", async () => {
  const env = makeEnv();
  await seed(env, { "s@kvantpro.com": { role: "sourcing", sites: [], tabs: [], note: "", seen: 1 } });
  assert.equal((await call(env, "/admin", "s@kvantpro.com")).status, 403);
  assert.equal((await call(env, "/admin/api", "s@kvantpro.com",
    { method: "POST", body: JSON.stringify({ op: "user_role", email: "s@kvantpro.com", role: "owner" }) })).status, 403);
  const acl = await loadAcl(env);
  assert.equal(acl.users["s@kvantpro.com"].role, "sourcing", "чужая правка прошла в хранилище");
});

test("владелец видит панель со всеми людьми, ролями и вкладками", async () => {
  const env = makeEnv();
  await seed(env, { "s@kvantpro.com": { role: "sourcing", sites: [], tabs: [], note: "", seen: 3 } });
  const r = await call(env, "/admin", "boss@kvantpro.com");
  assert.equal(r.status, 200);
  const html = await r.text();
  assert.ok(html.includes("s@kvantpro.com"), "в панели нет сотрудника");
  assert.ok(html.includes("Сорсинг") && html.includes("Советы знатока"), "в панели нет перечня вкладок");
  assert.ok(html.includes("/admin/api"), "панель не умеет сохранять");
});

test("владелец меняет роль, точечные права и роль по умолчанию — всё ложится в хранилище", async () => {
  const env = makeEnv();
  await seed(env, { "s@kvantpro.com": { role: "sourcing", sites: [], tabs: [], note: "", seen: 1 } });
  const post = (body) => call(env, "/admin/api", "boss@kvantpro.com",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  assert.equal((await (await post({ op: "user_role", email: "s@kvantpro.com", role: "kam" })).json()).ok, true);
  assert.equal((await loadAcl(env)).users["s@kvantpro.com"].role, "kam");

  await post({ op: "user_extra", email: "s@kvantpro.com", sites: ["gok", "марс"], tabs: ["advisor", "выдумка"], note: "по заявке" });
  const u = (await loadAcl(env)).users["s@kvantpro.com"];
  assert.deepEqual(u.sites, ["gok"], "несуществующий сайт не должен сохраняться");
  assert.deepEqual(u.tabs, ["advisor"]);
  assert.equal(u.note, "по заявке");

  await post({ op: "role_rights", role: "guest", sites: ["gpu"], tabs: ["eng"] });
  assert.deepEqual((await loadAcl(env)).roles.guest.tabs, ["eng"]);

  await post({ op: "default_role", role: "guest" });
  assert.equal((await loadAcl(env)).defaultRole, "guest");

  for (const [body, code] of [[{ op: "user_role", email: "", role: "kam" }, 404],
                              [{ op: "user_role", email: "s@kvantpro.com", role: "выдумка" }, 400],
                              [{ op: "выдумка" }, 400]]) {
    assert.equal((await post(body)).status, code, JSON.stringify(body));
  }
  assert.equal((await call(env, "/admin/api", "boss@kvantpro.com")).status, 405, "GET в приёмник правок не пускаем");
});

test("владелец заводит человека заранее и снимает с учёта", async () => {
  const env = makeEnv();
  await seed(env, {});
  const post = (body) => call(env, "/admin/api", "boss@kvantpro.com",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  assert.equal((await (await post({ op: "user_add", email: " Nov@KvantPro.com ", role: "kam" })).json()).ok, true);
  assert.equal((await loadAcl(env)).users["nov@kvantpro.com"].role, "kam");
  assert.equal((await post({ op: "user_add", email: "nov@kvantpro.com", role: "kam" })).status, 409, "повтор не заводим");
  assert.equal((await post({ op: "user_add", email: "не почта", role: "kam" })).status, 400);

  assert.equal((await (await post({ op: "user_drop", email: "nov@kvantpro.com" })).json()).ok, true);
  assert.equal((await loadAcl(env)).users["nov@kvantpro.com"], undefined);
  assert.equal((await post({ op: "user_drop", email: "nov@kvantpro.com" })).status, 404);
});

test("спецпроекты стоят под одной плашкой", async () => {
  const env = makeEnv();
  await seed(env, { "e@kvantpro.com": { role: "engineer", sites: [], tabs: [], note: "", seen: 1 } });
  const html = await (await call(env, "/", "e@kvantpro.com")).text();
  const sec = html.indexOf("Спецпроекты");
  assert.ok(sec > 0, "нет плашки «Спецпроекты»");
  const tail = html.slice(sec);
  for (const n of ["ОВЭ-75", "Гидрометаллургия", "Базовый проект ГОКа"]) {
    assert.ok(tail.includes(n), `${n} не попал в спецпроекты`);
  }
  assert.ok(html.indexOf("Библиотеки оборудования") < sec, "библиотеки должны идти выше спецпроектов");
});

test("права из ADMIN_EMAILS работают и без хранилища", async () => {
  const env = makeEnv({ ACL: undefined, VISITS: undefined });
  const r = await call(env, "/admin", "boss@kvantpro.com");
  assert.equal(r.status, 200);
  const save = await call(env, "/admin/api", "boss@kvantpro.com",
    { method: "POST", body: JSON.stringify({ op: "default_role", role: "guest" }) });
  assert.equal(save.status, 503, "без KV правка не должна выглядеть сохранённой");
  assert.match(await save.text(), /привяжите KV/);
});

test("страницы доступов не кэшируются и не индексируются", async () => {
  const env = makeEnv();
  for (const p of ["/", "/admin", "/dashboard"]) {
    const r = await call(env, p, "boss@kvantpro.com");
    assert.match(r.headers.get("Cache-Control") || "", /no-store/, p);
    assert.match(r.headers.get("X-Robots-Tag") || "", /noindex/, p);
  }
});
