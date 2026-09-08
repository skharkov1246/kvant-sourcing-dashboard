// Журнал действий: классификация, запись, признаки, оповещение, чтение.
// Запуск: node --test access/test/audit.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { auditRecord, signalsFor, raiseAlerts, readLog, classify, deviceOf, domainOf,
         SIGNALS, LOG_PREFIX, ALERT_PREFIX, NOTIFY_KEY, mskHourKey,
         loadNotify, saveNotify, sendAlert, alertText, tgResolveChat } from "../audit.js";

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
  assert.match(first[0].sent, /канал не настроен/, "без канала признак всё равно должен сохраниться");
  assert.equal([...env.ACL.box.keys()].filter((k) => k.startsWith(ALERT_PREFIX)).length, 1);

  const again = await raiseAlerts(env, { ...rec, t: new Date().toISOString() }, sig);
  assert.equal(again.length, 0, "повтор в тот же час не должен слать письмо");
});

// ── каналы оповещения ────────────────────────────────────────────────────────
// Подменяем сеть, чтобы видеть, куда и что ушло.
function net(handler) {
  const calls = [];
  const real = globalThis.fetch;
  globalThis.fetch = async (u, i) => { calls.push({ u: String(u), i }); return handler(String(u), i); };
  return { calls, restore: () => { globalThis.fetch = real; } };
}
const tgOk = () => new Response(JSON.stringify({ ok: true, result: {} }), { status: 200 });
const tgUpdates = (id) => new Response(JSON.stringify({ ok: true, result: [{ message: { chat: { id } } }] }), { status: 200 });

test("Telegram: переписка определяется сама и запоминается", async () => {
  const env = { ACL: kv() };
  await saveNotify(env, { tgToken: "111:AAA" });
  const n = net((u) => (u.includes("getUpdates") ? tgUpdates(-4242) : tgOk()));
  try {
    const note = await sendAlert(env, "проверка");
    assert.equal(note, "отправлено в Telegram");
    assert.match(n.calls[0].u, /getUpdates/, "адрес переписки должен определяться сам");
    assert.match(n.calls[1].u, /sendMessage/);
    assert.equal(JSON.parse(n.calls[1].i.body).chat_id, "-4242");
    assert.equal((await loadNotify(env)).tgChat, "-4242", "переписка не запомнена");

    // второй раз getUpdates уже не нужен — Telegram держит такие сообщения лишь сутки
    n.calls.length = 0;
    await sendAlert(env, "ещё раз");
    assert.equal(n.calls.length, 1);
    assert.match(n.calls[0].u, /sendMessage/);
  } finally { n.restore(); }
});

test("Telegram: без /start честно говорим, что делать", async () => {
  const env = { ACL: kv() };
  await saveNotify(env, { tgToken: "111:AAA" });
  const n = net(() => new Response(JSON.stringify({ ok: true, result: [] }), { status: 200 }));
  try {
    assert.match(await sendAlert(env, "проверка"), /напишите боту \/start/);
  } finally { n.restore(); }
});

test("Telegram: причина отказа доносится словами, а не молчанием", async () => {
  const env = { ACL: kv() };
  await saveNotify(env, { tgToken: "111:AAA", tgChat: "5" });
  const n = net(() => new Response(JSON.stringify({ ok: false, description: "chat not found" }), { status: 400 }));
  try {
    assert.match(await sendAlert(env, "проверка"), /telegram 400: chat not found/);
  } finally { n.restore(); }
});

test("ключ бота берётся и из переменной окружения", async () => {
  const env = { ACL: kv(), TG_TOKEN: "222:BBB", TG_CHAT: "77" };
  const n = net(() => tgOk());
  try {
    assert.equal(await sendAlert(env, "проверка"), "отправлено в Telegram");
    assert.match(n.calls[0].u, /bot222%3ABBB\/sendMessage/);
    assert.equal(JSON.parse(n.calls[0].i.body).chat_id, "77");
  } finally { n.restore(); }
});

test("почта остаётся запасным каналом, если Telegram не настроен", async () => {
  const env = { ACL: kv(), RESEND_API_KEY: "key", ALERT_TO: "boss@kvantpro.com", MAIL_FROM: "alerts@kvantpro.com" };
  const n = net(() => new Response("{}", { status: 200 }));
  try {
    assert.equal(await sendAlert(env, "a@kvantpro.com выгрузил 40 файлов за час"), "отправлено на boss@kvantpro.com");
    assert.match(n.calls[0].u, /api\.resend\.com/);
    const body = JSON.parse(n.calls[0].i.body);
    assert.deepEqual(body.to, ["boss@kvantpro.com"]);
    assert.match(body.subject, /выгрузил 40 файлов/);
  } finally { n.restore(); }
});

test("без каналов признак всё равно сохраняется — «не настроено» не значит «не заметили»", async () => {
  const env = { ACL: kv() };
  const rec = { t: new Date().toISOString(), em: "kto@gmail.com", dom: "gmail.com", site: "gpu", path: "/", geo: "NL" };
  const out = await raiseAlerts(env, rec, [{ id: "outside", text: "вход с чужой почты" }]);
  assert.match(out[0].sent, /канал не настроен/);
  assert.equal([...env.ACL.box.keys()].filter((k) => k.startsWith(ALERT_PREFIX)).length, 1);
});

test("в тексте оповещения есть суть, время, адрес и ссылка на журнал", () => {
  const t = alertText({ text: "выгрузка 40 файлов", t: "2026-09-08T09:00:00.000Z", site: "zip", path: "/x.csv", geo: "RU" });
  assert.match(t, /выгрузка 40 файлов/);
  assert.match(t, /2026-09-08/);
  assert.match(t, /\/x\.csv/);
  assert.match(t, /admin\/log/);
});

test("настройки канала лежат отдельным ключом и без хранилища не притворяются сохранёнными", async () => {
  const env = { ACL: kv() };
  assert.equal(await saveNotify(env, { tgToken: "x" }), true);
  assert.ok(env.ACL.box.has(NOTIFY_KEY));
  assert.equal(await saveNotify({}, { tgToken: "x" }), false);
  assert.deepEqual(await loadNotify({}), {});
});

test("определение переписки не падает на невнятном ответе", async () => {
  for (const r of [() => new Response("не json", { status: 200 }),
                   () => new Response("{}", { status: 500 }),
                   () => { throw new Error("сеть"); }]) {
    const n = net(r);
    try { assert.equal(await tgResolveChat("1:A"), null); } finally { n.restore(); }
  }
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
