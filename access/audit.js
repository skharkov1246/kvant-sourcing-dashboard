// ЖУРНАЛ ДЕЙСТВИЙ — канонический экземпляр. Блок между маркерами BEGIN/END auditCore
// вшит в public/_worker.js; совпадение копий проверяет access/test/audit.test.mjs.
//
// Что записываем: кто (почта и её домен), когда, какой сайт и какой адрес открыл,
// чем это было — просмотр страницы, выгрузка файла или правка прав, — из какой страны
// и с какого устройства. Записи живут полгода и лежат ТОЛЬКО в Cloudflare KV.
//
// РЕПОЗИТОРИЙ ПУБЛИЧНЫЙ. Журнал не выгружается в репозиторий ни при каких условиях:
// это сведения о сотрудниках. Смотреть их можно только владельцу в панели /admin/log.
//
// Сотрудников о ведении журнала следует уведомить — это и требование закона о
// персональных данных, и просто честный порядок. Текст уведомления — за владельцем.
//
// Оповещения. Признаки, при которых владельцу уходит письмо, перечислены в SIGNALS.
// Письма гасятся: один и тот же признак по одному человеку — не чаще раза в час.

import { normEmail, aclStore } from "./acl.js";

// BEGIN auditCore
const LOG_PREFIX = "log:";
const LOG_TTL = 180 * 24 * 3600;          // полгода
const CNT_TTL = 2 * 3600;                 // счётчики для распознавания всплесков
const ALERT_PREFIX = "alrt:";
const ALERT_TTL = 90 * 24 * 3600;
const MUTE_TTL = 3600;                    // повтор одного признака по человеку — раз в час

// Выгрузка — это файл, который человек уносит с собой. Картинки и шрифты сюда не входят:
// они часть страницы, а не данные.
const DOWNLOAD_RE = /\.(csv|tsv|xlsx?|pdf|jsonl?|zip|docx?|pptx?|txt|sql)$/i;
const ASSET_RE = /\.(css|js|mjs|map|woff2?|ttf|png|jpe?g|gif|svg|webp|ico|avif)$/i;

function classify(path) {
  if (path === "/gen" || path.startsWith("/fonts/")) return "skip";
  if (DOWNLOAD_RE.test(path)) return "download";
  if (ASSET_RE.test(path)) return "skip";
  if (path.startsWith("/admin/api")) return "admin";
  if (path.startsWith("/api/")) return "skip";     // служебный обмен между сайтами
  return "page";
}

const domainOf = (em) => String(em || "").split("@")[1] || "";
const corpDomains = (env) => String((env && env.CORP_DOMAINS) || "kvantpro.com")
  .split(/[,\s]+/).map((d) => d.trim().toLowerCase()).filter(Boolean);

// Устройство — грубо, одним словом: подробная строка браузера в журнале не нужна.
function deviceOf(ua) {
  const s = String(ua || "");
  if (!s) return "—";
  if (/bot|crawler|spider|curl|wget|python|node-fetch/i.test(s)) return "робот";
  if (/iPhone|iPad|Android|Mobile/i.test(s)) return "телефон";
  if (/Macintosh|Mac OS/i.test(s)) return "Mac";
  if (/Windows/i.test(s)) return "Windows";
  if (/Linux/i.test(s)) return "Linux";
  return "прочее";
}

// Признаки, при которых владельцу уходит письмо.
const SIGNALS = [
  { id: "outside", name: "вход с почты вне корпоративного домена" },
  { id: "first", name: "первый вход нового человека" },
  { id: "bulk", name: "массовая выгрузка файлов" },
  { id: "denied", name: "повторные попытки войти туда, где нет доступа" },
  { id: "robot", name: "обращения не из браузера" },
];

const pad = (n) => String(n).padStart(2, "0");
// Час по Москве — журнал и пороги считаем в том часовом поясе, в котором работает компания.
function mskHourKey(ms) {
  const d = new Date(ms + 3 * 3600 * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}`;
}

async function bump(kv, key, ttl) {
  const n = Number((await kv.get(key)) || 0) + 1;
  await kv.put(key, String(n), { expirationTtl: ttl });
  return n;
}

// Запись одного действия. Ничего не бросает: журнал не должен ломать отдачу страницы.
async function auditRecord(env, { request, email, site, path, kind, denied }) {
  const kv = aclStore(env);
  if (!kv) return null;
  const k = kind || classify(path);
  if (k === "skip" && !denied) return null;
  try {
    const em = normEmail(email);
    const now = Date.now();
    const h = request && request.headers;
    const rec = {
      t: new Date(now).toISOString(),
      em, dom: domainOf(em), site, path: String(path).slice(0, 200),
      kind: denied ? "denied" : k,
      geo: (h && h.get("CF-IPCountry")) || "—",
      dev: deviceOf(h && h.get("User-Agent")),
      ua: String((h && h.get("User-Agent")) || "").slice(0, 120),
    };
    await kv.put(`${LOG_PREFIX}${rec.t}:${Math.random().toString(36).slice(2, 6)}`,
                 JSON.stringify(rec), { expirationTtl: LOG_TTL });

    const hour = mskHourKey(now);
    const counts = {};
    if (rec.kind === "download") counts.dl = await bump(kv, `cnt:${em}:${hour}:dl`, CNT_TTL);
    if (rec.kind === "denied") counts.dn = await bump(kv, `cnt:${em}:${hour}:dn`, CNT_TTL);
    if (rec.dev === "робот") counts.rb = await bump(kv, `cnt:${em}:${hour}:rb`, CNT_TTL);
    return { rec, counts };
  } catch { return null; }
}

// Какие признаки сработали на этой записи.
function signalsFor(env, rec, counts, opts = {}) {
  const out = [];
  const lim = (name, def) => Number((env && env[name]) || def);
  if (rec.dom && !corpDomains(env).includes(rec.dom)) {
    out.push({ id: "outside", text: `вход с почты вне корпоративного домена: ${rec.em}` });
  }
  if (opts.firstEver) {
    out.push({ id: "first", text: `первый вход нового человека: ${rec.em}` });
  }
  if ((counts.dl || 0) >= lim("ALERT_DOWNLOADS", 30)) {
    out.push({ id: "bulk", text: `${rec.em} выгрузил ${counts.dl} файлов за час (порог ${lim("ALERT_DOWNLOADS", 30)})` });
  }
  if ((counts.dn || 0) >= lim("ALERT_DENIED", 5)) {
    out.push({ id: "denied", text: `${rec.em}: ${counts.dn} попыток открыть закрытое за час` });
  }
  if ((counts.rb || 0) >= lim("ALERT_ROBOT", 20)) {
    out.push({ id: "robot", text: `${rec.em}: ${counts.rb} обращений не из браузера за час` });
  }
  return out;
}

// Складывает сработавший признак и отправляет письмо. Возвращает список отправленного.
async function raiseAlerts(env, rec, signals) {
  const kv = aclStore(env);
  if (!kv || !signals.length) return [];
  const sent = [];
  for (const s of signals) {
    try {
      const mute = `mute:${s.id}:${rec.em}`;
      if (await kv.get(mute)) continue;                     // уже писали в этот час
      await kv.put(mute, "1", { expirationTtl: MUTE_TTL });
      const item = { t: rec.t, id: s.id, text: s.text, em: rec.em, site: rec.site, path: rec.path, geo: rec.geo };
      await kv.put(`${ALERT_PREFIX}${rec.t}:${s.id}`, JSON.stringify(item), { expirationTtl: ALERT_TTL });
      item.sent = await sendAlert(env, alertText(item));
      sent.push(item);
    } catch { /* оповещение не должно ломать отдачу страницы */ }
  }
  return sent;
}

// ОПОВЕЩЕНИЕ ВЛАДЕЛЬЦА. Канал выбирается тем, что настроено; признак сохраняется
// в любом случае — «канал не настроен» не должно означать «не заметили».
//
// Основной канал — Telegram: он не требует ни домена, ни DNS, ни учётной записи
// на стороннем сервисе. Владелец создаёт бота у @BotFather, пишет ему /start и
// вставляет ключ бота в панель; адрес переписки определяется сам (getUpdates) и
// запоминается. Почта оставлена запасным каналом на случай, если домен всё же
// подтвердят: тогда достаточно задать переменные, код уже готов.
const NOTIFY_KEY = "notify:v1";

async function loadNotify(env) {
  const kv = aclStore(env);
  if (!kv) return {};
  try { return (await kv.get(NOTIFY_KEY, { type: "json" })) || {}; } catch { return {}; }
}
async function saveNotify(env, cfg) {
  const kv = aclStore(env);
  if (!kv) return false;
  await kv.put(NOTIFY_KEY, JSON.stringify(cfg || {}));
  return true;
}

// Ключ бота: сначала из панели (хранилище), иначе из переменной окружения.
function tgToken(env, cfg) {
  return String((cfg && cfg.tgToken) || (env && env.TG_TOKEN) || "").trim();
}

// Адрес переписки. Telegram отдаёт его только после того, как человек написал боту,
// и держит такие сообщения сутки — поэтому найденное значение запоминаем навсегда.
async function tgResolveChat(token) {
  try {
    const r = await fetch(`https://api.telegram.org/bot${encodeURIComponent(token)}/getUpdates`);
    if (!r.ok) return null;
    const d = await r.json();
    const ups = (d && d.result) || [];
    for (let i = ups.length - 1; i >= 0; i--) {
      const m = ups[i].message || ups[i].edited_message || ups[i].channel_post;
      const id = m && m.chat && m.chat.id;
      if (id) return String(id);
    }
    return null;
  } catch { return null; }
}

async function tgSend(token, chat, text) {
  const r = await fetch(`https://api.telegram.org/bot${encodeURIComponent(token)}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chat, text, disable_web_page_preview: true }),
  });
  if (r.ok) return { ok: true };
  let why = `telegram ${r.status}`;
  try { const d = await r.json(); if (d && d.description) why += `: ${d.description}`; } catch { /* тело может быть пустым */ }
  return { ok: false, why };
}

// Отправка одного оповещения по настроенному каналу. Возвращает строку для журнала.
async function sendAlert(env, text, opts = {}) {
  const cfg = opts.cfg || (await loadNotify(env));
  const token = tgToken(env, cfg);
  if (token) {
    let chat = cfg.tgChat || (env && env.TG_CHAT) || null;
    if (!chat) {
      chat = await tgResolveChat(token);
      if (chat && !opts.noSave) { cfg.tgChat = chat; await saveNotify(env, cfg); }
    }
    if (!chat) return "Telegram: напишите боту /start — переписка ещё не начата";
    const r = await tgSend(token, chat, text);
    if (r.ok) return "отправлено в Telegram";
    return `не отправлено, ${r.why}`;
  }
  const to = String((env && env.ALERT_TO) || (env && env.ADMIN_EMAILS) || "").split(/[,\s]+/)[0];
  if (to && env && env.RESEND_API_KEY) {
    try {
      const r = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({ from: env.MAIL_FROM || "alerts@kvantpro.com", to: [to],
                               subject: `КВАНТ · доступы: ${text.split("\n")[0].slice(0, 120)}`, text }),
      });
      return r.ok ? `отправлено на ${to}` : `не отправлено: resend ${r.status}`;
    } catch (e) { return `не отправлено: ${String((e && e.message) || e)}`; }
  }
  return "канал не настроен — признак записан в журнал";
}

function alertText(item) {
  return [
    item.text, "",
    `время: ${item.t}`, `сайт: ${item.site || "—"}`, `адрес: ${item.path || "—"}`,
    `страна: ${item.geo}`, "", "Журнал: https://kvant-sourcing-f122.pages.dev/admin/log",
  ].join("\n");
}

// Чтение журнала для панели: свежие записи первыми. null — хранилище не привязано.
async function readLog(env, { prefix = LOG_PREFIX, limit = 300 } = {}) {
  const kv = aclStore(env);
  if (!kv) return null;
  const out = [];
  try {
    let cursor;
    do {
      const page = await kv.list({ prefix, cursor, limit: 1000 });
      for (const k of page.keys || []) out.push(k.name);
      cursor = page.list_complete ? null : page.cursor;
    } while (cursor && out.length < 5000);
    out.sort().reverse();
    const take = out.slice(0, limit);
    const recs = [];
    for (const name of take) {
      const v = await kv.get(name, { type: "json" });
      if (v) recs.push(v);
    }
    return recs;
  } catch { return null; }
}
// END auditCore

export {
  auditRecord, signalsFor, raiseAlerts, readLog, classify, deviceOf, domainOf,
  SIGNALS, LOG_PREFIX, ALERT_PREFIX, NOTIFY_KEY, mskHourKey,
  loadNotify, saveNotify, sendAlert, alertText, tgResolveChat,
};
