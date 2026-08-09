// Сквозной тест контура доступов: заявка → подтверждение владельцем → вход →
// резка дашборда по правам → отзыв. Запуск: node --test access/test/flow.test.mjs
//
// Тест гоняет ровно тот же gate.js, что поедет в Cloudflare, только с KV в памяти
// и ASSETS на локальной папке — никакой сети и никакого прода.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { handle, applyAcl, TAB_IDS } from "../gate.js";
import { buildIndex, testEnv } from "../contour.mjs";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kvant-acl-"));
const assetsDir = path.join(tmp, "public");
buildIndex(assetsDir);

const BASE = "https://test.local";
let env;
const jar = {};                                   // куки по «браузерам»: jar[who] = "kv_sid=..."

function reset() {
  env = testEnv({ assetsDir });
  env.SITE_URL = BASE;
  env.MAIL_LOG = "0";                             // не засорять вывод тестов письмами
  for (const k of Object.keys(jar)) delete jar[k];
}

async function req(pathname, { method = "GET", form, who, basic, cookie } = {}) {
  const headers = {};
  let body;
  if (form) {
    body = new URLSearchParams();
    for (const [k, v] of Object.entries(form)) (Array.isArray(v) ? v : [v]).forEach((x) => body.append(k, x));
    headers["Content-Type"] = "application/x-www-form-urlencoded";
  }
  if (basic) headers.Authorization = "Basic " + Buffer.from(basic).toString("base64");
  const c = cookie ?? (who ? jar[who] : null);
  if (c) headers.Cookie = c;
  const r = await handle(new Request(BASE + pathname, { method, headers, body }), env, { waitUntil: (p) => p });
  const set = r.headers.getSetCookie ? r.headers.getSetCookie() : [];
  if (who && set.length) jar[who] = set.map((s) => s.split(";")[0]).join("; ");
  const text = r.headers.get("content-type")?.includes("text/") || r.status < 300 ? await r.clone().text() : "";
  return { status: r.status, text, loc: r.headers.get("location"), setCookie: set, res: r };
}

// письма контура: провайдер "log" складывает их в KV под префиксом m:
async function outbox(to = null) {
  const page = await env.ACL.list({ prefix: "m:" });
  const all = [];
  for (const k of page.keys) all.push(JSON.parse(await env.ACL.get(k.name)));
  all.sort((a, b) => (a.at < b.at ? -1 : 1));
  return to ? all.filter((m) => m.to === to) : all;
}
const linkOf = (mail, marker) =>
  [...String(mail.html).matchAll(/https?:\/\/[^"'<\s]+/g)].map((m) => m[0]).find((u) => u.includes(marker));
const pathOf = (url) => url.slice(BASE.length);

// ---------------------------------------------------------------------------
test("аноним не видит ни дашборда, ни данных", async () => {
  reset();
  const r = await req("/");
  assert.equal(r.status, 200);
  assert.match(r.text, /Нужен доступ/);
  for (const m of ["MOCK-DATA", "MOCK-CONTRACTS", "MOCK-REPS", "MOCK-ADVISOR"]) {
    assert.ok(!r.text.includes(m), `в ответ анониму просочился ${m}`);
  }
});

test("полный цикл: заявка → письмо владельцу → подтверждение → вход → резка вкладок", async () => {
  reset();
  // 1. заявка на две вкладки
  const post = await req("/access/request", { method: "POST", form: {
    email: "petrov@kvant.ru", name: "Пётр Петров", org: "Отдел закупок",
    reason: "смотреть просрочки", tabs: ["contracts", "reps"] } });
  assert.equal(post.status, 200);
  assert.match(post.text, /Заявка отправлена/);

  // 2. владельцу ушло письмо со ссылкой подтверждения
  const ownerMail = (await outbox("skharkov@gmail.com")).at(-1);
  assert.ok(ownerMail, "письмо владельцу не отправлено");
  assert.match(ownerMail.html, /Реализация/);
  assert.match(ownerMail.html, /Коммерсанты/);
  const review = pathOf(linkOf(ownerMail, "/access/review"));
  assert.ok(review, "в письме владельцу нет ссылки подтверждения");

  // 3. страница решения открывается по ссылке из письма, галочки — по заявке
  const rev = await req(review);
  assert.equal(rev.status, 200);
  assert.match(rev.text, /petrov@kvant\.ru/);
  assert.match(rev.text, /value="contracts" checked/);
  assert.match(rev.text, /value="reps" checked/);

  // 4. владелец сужает набор: выдаёт только «Реализацию»
  const dec = await req(review, { method: "POST", form: { action: "approve", tabs: ["contracts"], days: "90" } });
  assert.equal(dec.status, 200);
  assert.match(dec.text, /Доступ выдан/);

  // 5. заявителю пришла одноразовая ссылка входа
  const userMail = (await outbox("petrov@kvant.ru")).at(-1);
  const enter = pathOf(linkOf(userMail, "/access/enter"));
  assert.ok(enter, "нет ссылки входа");
  const ent = await req(enter, { who: "petrov" });
  assert.equal(ent.status, 302);
  assert.equal(ent.loc, "/");
  assert.match(ent.setCookie.join(""), /kv_sid=/);
  assert.match(ent.setCookie.join(""), /HttpOnly/);

  // 6. дашборд отдан и порезан: своя вкладка есть, чужих — ни кнопок, ни данных
  const dash = await req("/", { who: "petrov" });
  assert.equal(dash.status, 200);
  assert.ok(dash.text.includes("MOCK-CONTRACTS"), "данные «Реализации» не отданы");
  assert.match(dash.text, /data-tab="contracts"/);
  for (const t of TAB_IDS.filter((x) => x !== "contracts")) {
    assert.ok(!dash.text.includes(`data-tab="${t}"`), `осталась кнопка чужой вкладки ${t}`);
  }
  for (const m of ["MOCK-DATA", "MOCK-INSIGHTS", "MOCK-COMPANY", "MOCK-REPS", "MOCK-ADVISOR", "MOCK-DIR"]) {
    assert.ok(!dash.text.includes(m), `в HTML просочились данные ${m}`);
  }
  assert.match(dash.text, /window\.__REPS__ = __kvNoData\(\);/);
  assert.match(dash.text, /window\.__DATA__ = __kvNoData\(\);/);
  assert.match(dash.text, /function __kvNoData\(\)/);   // пустышка вместо вырезанных данных
  assert.match(dash.text, /petrov@kvant\.ru/);        // плашка «кто вошёл»

  // 7. ссылка входа одноразовая
  const again = await req(enter);
  assert.equal(again.status, 200);
  assert.match(again.text, /уже использована/i);
});

test("отзыв доступа действует немедленно, сессия перестаёт работать", async () => {
  reset();
  await req("/access/request", { method: "POST",
    form: { email: "sidorov@kvant.ru", tabs: ["kam"] } });
  const review = pathOf(linkOf((await outbox("skharkov@gmail.com")).at(-1), "/access/review"));
  await req(review, { method: "POST", form: { action: "approve", tabs: ["kam"], days: "0" } });
  const enter = pathOf(linkOf((await outbox("sidorov@kvant.ru")).at(-1), "/access/enter"));
  await req(enter, { who: "sid" });
  assert.ok((await req("/", { who: "sid" })).text.includes("MOCK-DIR"));

  const rv = await req("/admin/user", { method: "POST", basic: "kvant:contour",
    form: { email: "sidorov@kvant.ru", action: "revoke" } });
  assert.equal(rv.status, 302);
  const after = await req("/", { who: "sid" });
  assert.match(after.text, /Нужен доступ/);
  assert.ok(!after.text.includes("MOCK-DIR"));
});

test("истёкший срок закрывает доступ", async () => {
  reset();
  await env.ACL.put("u:old@kvant.ru", JSON.stringify({
    email: "old@kvant.ru", tabs: ["reps"], exp: Date.now() - 1000 }));
  const login = await req("/access/login", { method: "POST", form: { email: "old@kvant.ru" } });
  assert.match(login.text, /Проверьте почту/);
  assert.equal((await outbox("old@kvant.ru")).length, 0, "просроченному нельзя слать ссылку входа");
});

test("отказ по заявке не создаёт пользователя", async () => {
  reset();
  await req("/access/request", { method: "POST", form: { email: "chuzhoy@mail.ru", tabs: ["advisor"] } });
  const review = pathOf(linkOf((await outbox("skharkov@gmail.com")).at(-1), "/access/review"));
  const dec = await req(review, { method: "POST", form: { action: "deny" } });
  assert.match(dec.text, /Отказано/);
  assert.equal(await env.ACL.get("u:chuzhoy@mail.ru"), null);
  const second = await req(review);                      // токен больше не даёт решать
  assert.match(second.text, /уже отклонена/);
});

test("подделанная кука и чужой токен не проходят", async () => {
  reset();
  const bad = await req("/", { cookie: "kv_sid=eyJrIjoic2lkIn0.AAAA" });
  assert.match(bad.text, /Нужен доступ/);
  const badRev = await req("/access/review?t=abc.def");
  assert.match(badRev.text, /недействительна/i);
});

test("админка закрыта для обычного пользователя и открыта владельцу", async () => {
  reset();
  await req("/access/request", { method: "POST", form: { email: "user@kvant.ru", tabs: ["reps"] } });
  const review = pathOf(linkOf((await outbox("skharkov@gmail.com")).at(-1), "/access/review"));
  await req(review, { method: "POST", form: { action: "approve", tabs: ["reps"], days: "0" } });
  const enter = pathOf(linkOf((await outbox("user@kvant.ru")).at(-1), "/access/enter"));
  await req(enter, { who: "u" });

  const denied = await req("/admin", { who: "u" });
  assert.match(denied.text, /Недостаточно прав/);
  const ok = await req("/admin", { basic: "kvant:contour" });
  assert.match(ok.text, /Доступы/);
  assert.match(ok.text, /user@kvant\.ru/);
});

test("заявка без вкладок отклоняется формой", async () => {
  reset();
  const r = await req("/access/request", { method: "POST", form: { email: "x@kvant.ru" } });
  assert.match(r.text, /Отметьте хотя бы одну вкладку/);
  assert.equal((await outbox("skharkov@gmail.com")).length, 0);
});

test("ограничение частоты заявок с одного адреса", async () => {
  reset();
  let last;
  for (let i = 0; i < 10; i++) {
    last = await req("/access/request", { method: "POST", form: { email: `f${i}@kvant.ru`, tabs: ["reps"] } });
  }
  assert.match(last.text, /Слишком много заявок/);
});

test("applyAcl: пары вкладок делят один массив данных — это отражено в правах", () => {
  const html = fs.readFileSync(path.join(assetsDir, "index.html"), "utf8");
  const who = { email: "a@b.ru", admin: false };
  // «Поставщики» строятся из __CONTRACTS__ — выдача только их сохраняет payload
  const sup = applyAcl(html, ["suppliers"], who);
  assert.ok(sup.includes("MOCK-SUPPLIERS"));
  assert.match(sup, /data-tab="suppliers"/);
  assert.ok(!/data-tab="contracts"/.test(sup), "кнопка «Реализация» не должна остаться");
  // «Когорты» — из __COMPANY__
  const coh = applyAcl(html, ["cohorts"], who);
  assert.ok(coh.includes("MOCK-COMPANY"));
  assert.match(coh, /window\.__CONTRACTS__ = __kvNoData\(\);/);
  // полный доступ ничего не режет
  const all = applyAcl(html, TAB_IDS, { email: "own@kvant.ru", admin: true });
  for (const t of TAB_IDS) assert.match(all, new RegExp(`data-tab="${t}"`));
  assert.ok(!all.includes("__kvNoData"), 'при полном доступе пустышка не нужна');
});

test("панель «Сорсинг» прячется, если вкладка не выдана", () => {
  const html = fs.readFileSync(path.join(assetsDir, "index.html"), "utf8");
  const cut = applyAcl(html, ["reps"], { email: "a@b.ru", admin: false });
  assert.match(cut, /<div id="tab-sourcing" hidden>/);
  assert.match(cut, /querySelector\('\.tabs \.tab'\)/);      // автопереход на первую доступную
  const keep = applyAcl(html, ["sourcing"], { email: "a@b.ru", admin: false });
  assert.match(keep, /<div id="tab-sourcing">/);
});

test("владелец заводит себя сам и получает админку", async () => {
  reset();
  const r = await req("/access/login", { method: "POST", form: { email: "skharkov@gmail.com" } });
  assert.match(r.text, /Проверьте почту/);
  const enter = pathOf(linkOf((await outbox("skharkov@gmail.com")).at(-1), "/access/enter"));
  const ent = await req(enter, { who: "own" });
  assert.equal(ent.status, 302);
  const adm = await req("/admin", { who: "own" });
  assert.match(adm.text, /Доступы/);
  const dash = await req("/", { who: "own" });
  for (const t of TAB_IDS) assert.match(dash.text, new RegExp(`data-tab="${t}"`));
  assert.ok(!dash.text.includes("__kvNoData"), "владельцу данные не режем");
});

test("аварийный вход по паролю вызывает 401, с паролем ведёт в админку", async () => {
  reset();
  const challenge = await req("/access/basic");
  assert.equal(challenge.status, 401);
  assert.match(challenge.res.headers.get("WWW-Authenticate") || "", /Basic realm/);
  const ok = await req("/access/basic", { basic: "kvant:contour", who: "own" });
  assert.equal(ok.status, 302);
  assert.equal(ok.loc, "/admin");
  assert.match(ok.setCookie.join(""), /kv_sid=/);          // дальше владелец ходит по куке
  assert.match((await req("/admin", { who: "own" })).text, /Доступы/);
  const bad = await req("/access/basic", { basic: "kvant:wrong" });
  assert.equal(bad.status, 401);
});
