// Журнал действий: классификация, запись, признаки, оповещение, чтение.
// Запуск: node --test access/test/audit.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { auditRecord, signalsFor, raiseAlerts, readLog, classify, deviceOf, domainOf,
         SIGNALS, LOG_PREFIX, ALERT_PREFIX, mskHourKey } from "../audit.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

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
const req = (ua = "Mozilla/5.0 (Macintosh)", geo = "RU") =>
  new Request("https://x/", { headers: { "User-Agent": ua, "CF-IPCountry": geo } });

test("что считается действием, а что частью страницы", () => {
  assert.equal(classify("/dashboard"), "page");
  assert.equal(classify("/orders/pilot-order-1.pdf"), "download");
  assert.equal(classify("/data/positions.csv"), "download");
  assert.equal(classify("/export.xlsx"), "download");
  assert.equal(classify("/admin/api"), "admin");
  for (const p of ["/app.css", "/app.js", "/logo.svg", "/f.woff2", "/photo.jpg"]) {
    assert.equal(classify(p), "skip", p);
  }
  assert.equal(classify("/gen"), "skip", "опрос свежести — не действие");
  assert.equal(classify("/fonts/x.woff2"), "skip");
  assert.equal(classify("/api/rights"), "skip", "служебный обмен между сайтами не пишем");
});

test("устройство и домен разбираются по строке браузера и почте", () => {
  assert.equal(deviceOf("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"), "телефон");
  assert.equal(deviceOf("Mozilla/5.0 (Windows NT 10.0)"), "Windows");
  assert.equal(deviceOf("curl/8.4.0"), "робот");
  assert.equal(deviceOf("python-requests/2.31"), "робот");
  assert.equal(deviceOf(""), "—");
  assert.equal(domainOf("Ivan@KvantPro.com"), "KvantPro.com");
  assert.equal(domainOf("нет-собаки"), "");
  assert.equal(domainOf(""), "");
});

test("запись действия ложится в хранилище со всем нужным", async () => {
  const env = { ACL: kv() };
  const out = await auditRecord(env, { request: req(), email: "Ivan@KvantPro.com", site: "gpu", path: "/lot.html" });
  assert.ok(out, "действие не записано");
  assert.equal(out.rec.em, "ivan@kvantpro.com");
  assert.equal(out.rec.dom, "kvantpro.com");
  assert.equal(out.rec.site, "gpu");
  assert.equal(out.rec.kind, "page");
  assert.equal(out.rec.geo, "RU");
  assert.equal(out.rec.dev, "Mac");
  const keys = [...env.ACL.box.keys()].filter((k) => k.startsWith(LOG_PREFIX));
  assert.equal(keys.length, 1);
});

test("части страницы не пишутся, отказ пишется всегда", async () => {
  const env = { ACL: kv() };
  assert.equal(await auditRecord(env, { request: req(), email: "a@kvantpro.com", site: "gpu", path: "/app.css" }), null);
  const denied = await auditRecord(env, { request: req(), email: "a@kvantpro.com", site: "gok", path: "/app.css", denied: true });
  assert.equal(denied.rec.kind, "denied", "отказ важен, даже если это был файл разметки");
});

test("без хранилища журнал молча ничего не делает", async () => {
  assert.equal(await auditRecord({}, { request: req(), email: "a@kvantpro.com", site: "gpu", path: "/" }), null);
  assert.equal(await readLog({}), null, "отсутствие хранилища нельзя показывать как пустой журнал");
});

test("почта вне корпоративного домена поднимает признак", () => {
  const rec = { em: "kto@gmail.com", dom: "gmail.com" };
  const s = signalsFor({}, rec, {});
  assert.ok(s.some((x) => x.id === "outside"), "чужая почта должна отмечаться");
  assert.equal(signalsFor({}, { em: "a@kvantpro.com", dom: "kvantpro.com" }, {}).length, 0);
  // список корпоративных доменов настраивается
  assert.equal(signalsFor({ CORP_DOMAINS: "gmail.com" }, rec, {}).length, 0);
});

test("первый вход, массовая выгрузка, отказы и роботы — каждый со своим порогом", () => {
  const rec = { em: "a@kvantpro.com", dom: "kvantpro.com" };
  assert.ok(signalsFor({}, rec, {}, { firstEver: true }).some((x) => x.id === "first"));
  assert.equal(signalsFor({}, rec, { dl: 29 }).length, 0, "порог по умолчанию — 30 выгрузок за час");
  assert.ok(signalsFor({}, rec, { dl: 30 }).some((x) => x.id === "bulk"));
  assert.ok(signalsFor({ ALERT_DOWNLOADS: 3 }, rec, { dl: 3 }).some((x) => x.id === "bulk"), "порог настраивается");
  assert.ok(signalsFor({}, rec, { dn: 5 }).some((x) => x.id === "denied"));
  assert.ok(signalsFor({}, rec, { rb: 20 }).some((x) => x.id === "robot"));
  const ids = SIGNALS.map((x) => x.id);
  for (const id of ["outside", "first", "bulk", "denied", "robot"]) assert.ok(ids.includes(id), id);
});

test("счётчик выгрузок за час растёт и доводит до признака", async () => {
  const env = { ACL: kv(), ALERT_DOWNLOADS: 3 };
  let last;
  for (let i = 0; i < 3; i++) {
    last = await auditRecord(env, { request: req(), email: "a@kvantpro.com", site: "zip", path: `/orders/f${i}.pdf` });
  }
  assert.equal(last.counts.dl, 3);
  assert.ok(signalsFor(env, last.rec, last.counts).some((x) => x.id === "bulk"));
});

test("признак сохраняется, и по одному человеку не чаще раза в час", async () => {
  const env = { ACL: kv(), ADMIN_EMAILS: "boss@kvantpro.com" };
  const rec = { t: new Date().toISOString(), em: "kto@gmail.com", dom: "gmail.com", site: "gpu", path: "/", geo: "NL" };
  const sig = [{ id: "outside", text: "вход с почты вне корпоративного домена: kto@gmail.com" }];
  const first = await raiseAlerts(env, rec, sig);
  assert.equal(first.length, 1);
  assert.match(first[0].mail, /почта не настроена/, "без ключа признак всё равно должен сохраниться");
  assert.equal([...env.ACL.box.keys()].filter((k) => k.startsWith(ALERT_PREFIX)).length, 1);

  const again = await raiseAlerts(env, { ...rec, t: new Date().toISOString() }, sig);
  assert.equal(again.length, 0, "повтор в тот же час не должен слать письмо");
});

test("письмо уходит, когда провайдер настроен", async () => {
  const real = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (u, i) => { calls.push({ u: String(u), i }); return new Response("{}", { status: 200 }); };
  try {
    const env = { ACL: kv(), RESEND_API_KEY: "key", ALERT_TO: "boss@kvantpro.com", MAIL_FROM: "alerts@kvantpro.com" };
    const rec = { t: new Date().toISOString(), em: "a@kvantpro.com", dom: "kvantpro.com", site: "zip", path: "/x.csv", geo: "RU" };
    const out = await raiseAlerts(env, rec, [{ id: "bulk", text: "a@kvantpro.com выгрузил 40 файлов за час" }]);
    assert.match(out[0].mail, /отправлено на boss@kvantpro.com/);
    assert.equal(calls.length, 1);
    assert.match(calls[0].u, /api\.resend\.com/);
    const body = JSON.parse(calls[0].i.body);
    assert.deepEqual(body.to, ["boss@kvantpro.com"]);
    assert.match(body.subject, /выгрузил 40 файлов/);
    assert.match(body.text, /admin\/log/, "в письме должна быть ссылка на журнал");
  } finally { globalThis.fetch = real; }
});

test("журнал читается свежими записями вперёд", async () => {
  const env = { ACL: kv() };
  for (const p of ["/a", "/b", "/c"]) {
    await auditRecord(env, { request: req(), email: "a@kvantpro.com", site: "gpu", path: p });
    await new Promise((r) => setTimeout(r, 2));
  }
  const recs = await readLog(env, { limit: 2 });
  assert.equal(recs.length, 2, "ограничение по количеству не соблюдено");
  assert.equal(recs[0].path, "/c", "свежая запись должна быть первой");
  assert.equal(recs[1].path, "/b");
});

test("час считается по Москве — пороги привязаны к рабочему дню компании", () => {
  // 31 декабря 22:30 UTC = 1 января 01:30 МСК
  const k = mskHourKey(Date.UTC(2026, 11, 31, 22, 30));
  assert.equal(k, "2027-01-01T01");
});

test("копия блока журнала в гейте портала совпадает с каноническим access/audit.js", () => {
  const block = (s) => { const m = s.match(/\/\/ BEGIN auditCore[\s\S]*?\/\/ END auditCore/); return m ? m[0] : null; };
  const want = block(fs.readFileSync(path.join(ROOT, "access/audit.js"), "utf8"));
  assert.ok(want, "в access/audit.js нет маркеров");
  assert.equal(block(fs.readFileSync(path.join(ROOT, "public/_worker.js"), "utf8")), want,
    "public/_worker.js: блок auditCore разошёлся с access/audit.js");
});

test("журнал не попадает в репозиторий", () => {
  // сведения о сотрудниках хранятся только в Cloudflare KV
  const src = fs.readFileSync(path.join(ROOT, "access/audit.js"), "utf8");
  assert.doesNotMatch(src, /repository_dispatch|api\.github\.com|contents\//,
    "журнал не должен уметь писать в GitHub");
});
