// Портал КВАНТ, дашборд сорсинга и управление доступами.
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы через этот
// fetch; файлы отдаём через env.ASSETS уже после проверки подписи входа Cloudflare Access.
//
// Маршруты:  /            — портал: плитки доступных человеку сайтов
//            /dashboard   — дашборд сорсинга, вкладки режутся по правам
//            /admin       — панель управления доступами (только владельцу)
//            /admin/api   — приём правок из панели
//            /api/rights  — права вошедшего для гейтов остальных сайтов
//            /gen         — метка свежести · /fonts/* — иммутабельная статика
//
// Периметр держит Cloudflare Access (политика по домену почты), паролей нет.
// Права внутри — модуль ниже (канонический экземпляр: access/acl.js), хранилище — KV:
// привязка ACL, а при её отсутствии используется уже привязанная VISITS.
//
// САМООБНОВЛЕНИЕ: если данные старше 2 ч, воркер триггерит пересборку через
// GitHub repository_dispatch (секрет GH_DISPATCH_TOKEN); без секрета просто выключено.
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const FRESH_MS = 2 * 3600 * 1000;          // порог свежести — 2 часа
const DEBOUNCE_MS = 15 * 60;               // не триггерить пересборку чаще раза в 15 мин

// BEGIN aclCore
const ACL_KEY = "acl:v1";
// Версия документа прав. 2 — «База ЗИП» разделена на две плитки: ГШО и ГТУ-библиотека.
// Разделение не должно молча отнимать доступ, поэтому при подъёме версии тем, у кого
// был ЗИП, добавляется ГТУ (справочник и раньше лежал внутри того же сайта).
const ACL_VERSION = 2;

// Разделы портала. Спецпроекты ведёт один департамент, поэтому стоят под общей плашкой
// (распоряжение владельца от 07.09.2026).
const GROUPS = [
  { id: "work", name: "Работа с данными", note: "ежедневные инструменты" },
  { id: "lib", name: "Библиотеки оборудования", note: "ГПУ, ГТУ, ГШО — поставщики, цены, аналоги" },
  { id: "proj", name: "Спецпроекты", note: "проработки под заказчика; ведёт один департамент" },
];

// Сайты компании. gt (библиотека ГТУ) живёт по пути внутри проекта ЗИП,
// поэтому отдельным проектом Pages не является — но правом управляется отдельно.
const SITES = [
  { id: "dashboard", group: "work", name: "Дашборд сорсинга", href: "/dashboard",
    note: "нагрузка, конверсия, реализация, поставщики — по данным Bitrix24" },
  { id: "zip", group: "lib", name: "ГШО — горно-шахтное оборудование", href: "https://kvant-zip.pages.dev/",
    note: "перфораторы, буровая и горнопроходческая техника: позиции, ODM-аналоги, цены, таможня, заказы" },
  { id: "gt", group: "lib", name: "ГТУ — газотурбинные установки", href: "https://kvant-zip.pages.dev/gt/",
    note: "Siemens SGT-100…400 и SGT5-4000F, GE LM6000 и Frame 6B: субпоставщики, MRO, склады" },
  { id: "gpu", group: "lib", name: "ГПУ — газопоршневые установки", href: "https://kvant-gpu.pages.dev/",
    note: "Cummins, Caterpillar, INNIO: поставщики, цены, разрывы OEM/аналог" },
  { id: "ove", group: "proj", name: "ОВЭ-75", href: "https://kvant-ove.pages.dev/",
    note: "обжиг, выщелачивание, электроэкстракция — проект для Кольской ГМК" },
  { id: "gidromet", group: "proj", name: "Гидрометаллургия", href: "https://kvant-gidromet.pages.dev/",
    note: "заключение по переработке медно-золотого концентрата" },
  { id: "gok", group: "proj", name: "Базовый проект ГОКа", href: "https://kvant-gok.pages.dev/",
    note: "золото-медный горно-обогатительный комбинат" },
];

// Вкладки дашборда. pay — массивы данных, которые вкладка читает: если ни одна
// доступная вкладка не просит массив, он вырезается из страницы на сервере.
const TABS = [
  { id: "sourcing", name: "Сорсинг", pay: ["DATA", "INSIGHTS"] },
  { id: "company", name: "Пульс компании", pay: ["COMPANY"] },
  { id: "kam", name: "КАМы", pay: ["KAM"] },
  { id: "eng", name: "Инжиниринг", pay: ["ENG"] },
  { id: "prod", name: "Продукт-оунеры", pay: ["PRODUCT"] },
  { id: "reps", name: "Коммерсанты", pay: ["REPS"] },
  { id: "contracts", name: "Реализация", pay: ["CONTRACTS"] },
  { id: "suppliers", name: "Поставщики", pay: ["CONTRACTS"] },
  { id: "cohorts", name: "Когорты", pay: ["COMPANY"] },
  { id: "advisor", name: "Советы знатока", pay: ["ADVISOR"] },
];
const PAYLOADS = ["DATA", "INSIGHTS", "COMPANY", "KAM", "ENG", "PRODUCT", "CONTRACTS", "REPS", "ADVISOR"];
const SITE_IDS = SITES.map((s) => s.id);
const TAB_IDS = TABS.map((t) => t.id);

// Роли по умолчанию. Владелец правит их в админке; здесь — состояние при первом запуске.
function defaultAcl() {
  return {
    version: ACL_VERSION,
    defaultRole: "employee",
    roles: {
      owner: { name: "Владелец", admin: true, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      head: { name: "Руководитель", admin: false, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      employee: { name: "Сотрудник", admin: false, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      sourcing: { name: "Сорсинг", admin: false,
        sites: ["dashboard", "zip", "gt", "gpu"], tabs: ["sourcing", "contracts", "suppliers"] },
      kam: { name: "КАМ", admin: false,
        sites: ["dashboard", "zip", "gt"], tabs: ["company", "kam", "reps", "cohorts"] },
      engineer: { name: "Инженер", admin: false,
        sites: ["zip", "gt", "gpu", "ove", "gidromet", "gok"], tabs: [] },
      guest: { name: "Гость", admin: false, sites: [], tabs: [] },
    },
    users: {},
  };
}

function normEmail(s) { return String(s || "").trim().toLowerCase(); }

// Разбор документа из хранилища: чинит недостающее, чтобы панель не падала на старых данных.
function normalizeAcl(raw) {
  const d = defaultAcl();
  if (!raw || typeof raw !== "object") return d;
  const old = Number(raw.version || 1) < 2;
  const sites = (list) => {
    const ok = (Array.isArray(list) ? list : []).filter((x) => SITE_IDS.includes(x));
    if (old && ok.includes("zip") && !ok.includes("gt")) ok.push("gt");
    return SITE_IDS.filter((x) => ok.includes(x));
  };
  const roles = { ...d.roles };
  for (const [id, r] of Object.entries(raw.roles || {})) {
    if (!r || typeof r !== "object") continue;
    roles[id] = {
      name: String(r.name || id),
      admin: !!r.admin,
      sites: sites(r.sites),
      tabs: (Array.isArray(r.tabs) ? r.tabs : []).filter((x) => TAB_IDS.includes(x)),
    };
  }
  const defaultRole = roles[raw.defaultRole] ? raw.defaultRole : d.defaultRole;
  const users = {};
  for (const [em, u] of Object.entries(raw.users || {})) {
    if (!u || typeof u !== "object") continue;
    users[normEmail(em)] = {
      role: roles[u.role] ? u.role : defaultRole,
      sites: sites(u.sites),
      tabs: (Array.isArray(u.tabs) ? u.tabs : []).filter((x) => TAB_IDS.includes(x)),
      note: String(u.note || "").slice(0, 200),
      first: String(u.first || ""),
      last: String(u.last || ""),
      seen: Number(u.seen || 0),
    };
  }
  return { version: ACL_VERSION, defaultRole, roles, users };
}

function aclStore(env) { return (env && (env.ACL || env.VISITS)) || null; }

async function loadAcl(env) {
  const kv = aclStore(env);
  if (!kv) return defaultAcl();
  try {
    const raw = await kv.get(ACL_KEY, { type: "json" });
    return normalizeAcl(raw);
  } catch { return defaultAcl(); }
}

async function saveAcl(env, acl) {
  const kv = aclStore(env);
  if (!kv) return false;
  await kv.put(ACL_KEY, JSON.stringify(normalizeAcl(acl)));
  return true;
}

// Права конкретного человека: роль плюс точечные добавки. Владельцы из ADMIN_EMAILS
// получают полный набор всегда — это страховка от потери доступа к хранилищу.
function rightsFor(acl, email, env) {
  const em = normEmail(email);
  const hard = String((env && env.ADMIN_EMAILS) || "stepan@kvantpro.com")
    .split(/[,\s]+/).map(normEmail).filter(Boolean);
  if (hard.includes(em)) {
    return { email: em, role: "owner", roleName: "Владелец", admin: true,
             sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() };
  }
  const u = acl.users[em];
  const roleId = (u && u.role) || acl.defaultRole;
  const role = acl.roles[roleId] || acl.roles[acl.defaultRole] || { name: roleId, admin: false, sites: [], tabs: [] };
  const sites = new Set(role.sites);
  const tabs = new Set(role.tabs);
  if (u) {
    for (const s of u.sites) sites.add(s);
    for (const t of u.tabs) tabs.add(t);
  }
  return {
    email: em, role: roleId, roleName: role.name, admin: !!role.admin,
    sites: SITE_IDS.filter((x) => sites.has(x)),
    tabs: TAB_IDS.filter((x) => tabs.has(x)),
  };
}

// УЧЁТ ВХОДОВ. Держится отдельными ключами seen:<почта>, а не внутри документа прав,
// по двум причинам: запись входа не должна затирать правку, сделанную в это же время
// в панели, и не должна переписывать весь документ на каждый заход.
//
// Отметка ставится на любой странице, которую отдаёт портал, и на /api/rights —
// то есть и когда человек заходит сразу на дашборд или на другой сайт по закладке.
// Чтобы не писать в хранилище на каждый запрос, повторная отметка в пределах
// SEEN_QUIET пропускается.
const SEEN_PREFIX = "seen:";
const SEEN_QUIET = 10 * 60 * 1000;

// Возвращает true, если это первый вход человека вообще: журнал отмечает такое
// отдельным признаком, чтобы владелец узнавал о новых людях сразу.
async function touchUser(env, email) {
  const em = normEmail(email);
  if (!em) return false;
  const kv = aclStore(env);
  if (!kv) return false;
  try {
    const key = SEEN_PREFIX + em;
    const prev = (await kv.get(key, { type: "json" })) || {};
    const now = Date.now();
    if (prev.last && now - Date.parse(prev.last) < SEEN_QUIET) return false;
    const iso = new Date(now).toISOString();
    await kv.put(key, JSON.stringify({ first: prev.first || iso, last: iso, seen: Number(prev.seen || 0) + 1 }));
    return !prev.first;
  } catch { return false; }
}

// Кто и когда заходил. Возвращает null, если хранилище не привязано, — панель обязана
// отличать «никто не заходил» от «учёт вообще не ведётся».
async function loadSeen(env) {
  const kv = aclStore(env);
  if (!kv) return null;
  const out = {};
  try {
    let cursor;
    do {
      const page = await kv.list({ prefix: SEEN_PREFIX, cursor });
      for (const k of page.keys || []) {
        const v = await kv.get(k.name, { type: "json" });
        if (v) out[k.name.slice(SEEN_PREFIX.length)] = v;
      }
      cursor = page.list_complete ? null : page.cursor;
    } while (cursor);
  } catch { return null; }
  return out;
}

// Вырезание вкладок дашборда: убираем кнопку, панель и — главное — массив данных.
// Пустышка нужна потому, что шапка «Сорсинга» читает данные сразу при загрузке
// и на голом null падает вместе с переключателем вкладок.
const NO_DATA_STUB = '<script>function __kvNoData(){return new Proxy(function(){},{' +
  'get:function(t,p){' +
  "if(p===Symbol.toPrimitive)return function(){return ''};" +
  'if(p===Symbol.iterator)return function*(){};' +
  "if(p==='length')return 0;" +
  "if(p==='toJSON'||p==='then'||p===Symbol.toStringTag)return undefined;" +
  'return __kvNoData()},' +
  'apply:function(){return __kvNoData()},' +
  'construct:function(){return __kvNoData()}})}</script>';

function cutDashboard(html, allowedTabs) {
  const allow = new Set(allowedTabs);
  let out = html;
  // кнопки чужих вкладок
  out = out.replace(/<button class="tab[^"]*" data-tab="([a-z]+)"[^>]*>[\s\S]*?<\/button>/g,
    (m, id) => (allow.has(id) ? m : ""));
  // пустые панели чужих вкладок (наполняются из JS)
  for (const t of TAB_IDS) {
    if (allow.has(t) || t === "sourcing") continue;
    out = out.replace(new RegExp(`<div id="tab-${t}" hidden></div>`), "");
  }
  // массивы данных: остаётся только то, что просит хотя бы одна доступная вкладка
  const need = new Set();
  for (const t of TABS) if (allow.has(t.id)) for (const p of t.pay) need.add(p);
  const cut = PAYLOADS.filter((p) => !need.has(p));
  for (const p of cut) {
    out = out.replace(new RegExp(`window\\.__${p}__ *= *[\\s\\S]*?;\\n`), `window.__${p}__ = __kvNoData();\n`);
  }
  if (cut.length) out = out.replace("<body>", "<body>\n" + NO_DATA_STUB);
  // «Сорсинг» — единственная вкладка со статической разметкой: прячем и открываем первую доступную
  if (!allow.has("sourcing")) {
    out = out.replace('<div id="tab-sourcing">', '<div id="tab-sourcing" hidden>');
    out = out.replace("</body>", "<script>(function(){var b=document.querySelector('.tabs .tab');if(b)b.click();})();</script></body>");
  }
  return out;
}
// END aclCore

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
      item.mail = await alertMail(env, item);
      sent.push(item);
    } catch { /* оповещение не должно ломать отдачу страницы */ }
  }
  return sent;
}

// Письмо владельцу. Провайдер задаётся переменными; без ключа признак всё равно
// сохраняется и виден в панели — «не настроена почта» не должно означать «не заметили».
async function alertMail(env, item) {
  const to = String((env && env.ALERT_TO) || (env && env.ADMIN_EMAILS) || "").split(/[,\s]+/)[0];
  if (!to || !env.RESEND_API_KEY) return "почта не настроена — признак записан в панель";
  const subject = `КВАНТ · доступы: ${item.text.slice(0, 120)}`;
  const body = [
    item.text, "",
    `время: ${item.t}`, `сайт: ${item.site || "—"}`, `адрес: ${item.path || "—"}`,
    `страна: ${item.geo}`, "", "Журнал: https://kvant-sourcing-f122.pages.dev/admin/log",
  ].join("\n");
  try {
    const r = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: env.MAIL_FROM || "alerts@kvantpro.com", to: [to], subject, text: body }),
    });
    return r.ok ? `отправлено на ${to}` : `не отправлено: resend ${r.status}`;
  } catch (e) { return `не отправлено: ${String((e && e.message) || e)}`; }
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

export default {
  async fetch(request, env, ctx) {
    const who = await accessOk(request, env);
    if (!who) return denyPage("Портал КВАНТ");

    const url = new URL(request.url);
    const acl = await loadAcl(env);
    const rights = rightsFor(acl, who.email, env);

    // Отметка о входе. Ставится и на /api/rights, поэтому в списке оказываются и те,
    // кто зашёл сразу на дашборд или на другой сайт по закладке, минуя портал.
    // Служебные запросы (шрифты, опрос свежести) не считаем.
    let firstEver = false;
    if (!url.pathname.startsWith("/fonts/") && url.pathname !== "/gen") {
      firstEver = await touchUser(env, who.email);
      ctx.waitUntil(audit(env, request, who, "portal", url.pathname, { firstEver }));
    }

    // /api/rights — права для гейтов остальных сайтов: они шлют сюда JWT вошедшего,
    // мы его проверяем тем же помощником и отвечаем набором прав. Общих секретов не нужно.
    if (url.pathname === "/api/rights") {
      // гейт сайта присылает, какой адрес человек открыл: журналу нужны все сайты,
      // а не только портал. Отсутствие параметров ничего не ломает.
      const site = url.searchParams.get("site") || "";
      const at = url.searchParams.get("at") || "";
      if (site && at) {
        const denied = !rights.sites.includes(site);
        ctx.waitUntil(audit(env, request, who, site, at, { denied, firstEver }));
      }
      return new Response(JSON.stringify({ email: rights.email, sites: rights.sites, tabs: rights.tabs, admin: rights.admin }),
        { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
    }

    // админка — только владельцу
    if (url.pathname === "/admin" || url.pathname === "/admin/") {
      if (!rights.admin) {
        ctx.waitUntil(audit(env, request, who, "admin", url.pathname, { denied: true }));
        return denyPage("Доступы · КВАНТ");
      }
      return adminPage(acl, who, env, await loadSeen(env));
    }
    if (url.pathname === "/admin/log" || url.pathname === "/admin/log/") {
      if (!rights.admin) {
        ctx.waitUntil(audit(env, request, who, "admin", url.pathname, { denied: true }));
        return denyPage("Журнал · КВАНТ");
      }
      return logPage(env, who, url);
    }
    if (url.pathname === "/admin/api") {
      if (!rights.admin) return new Response(JSON.stringify({ ok: false, error: "forbidden" }),
        { status: 403, headers: { "Content-Type": "application/json" } });
      ctx.waitUntil(audit(env, request, who, "admin", url.pathname, { kind: "admin" }));
      return adminApi(request, env, acl);
    }

    // портал — корень
    if (url.pathname === "/" || url.pathname === "/portal" || url.pathname === "/portal/") {
      if (env.VISITS && request.headers.get("X-Poll") !== "1") ctx.waitUntil(logVisit(request, env));
      return portalPage(who, rights, env);
    }

    // /gen — метка свежести для поллинга со страницы дашборда
    if (url.pathname === "/gen") {
      const idx = await env.ASSETS.fetch(new Request(url.origin + "/index.html", { headers: request.headers }));
      const text = await idx.text();
      const m = text.match(/gen=new Date\("([^"]+)"\)/);
      const genMs = m ? Date.parse(m[1]) : NaN;
      const stale = isNaN(genMs) || (Date.now() - genMs) > FRESH_MS;
      if (stale && env.GH_DISPATCH_TOKEN) ctx.waitUntil(triggerRebuild(env));
      return new Response(JSON.stringify({ gen: m ? m[1] : null }),
        { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
    }

    // шрифты — иммутабельная статика
    if (url.pathname.startsWith("/fonts/")) {
      const font = await env.ASSETS.fetch(request);
      const fh = new Headers(font.headers);
      fh.set("Cache-Control", "public, max-age=31536000, immutable");
      return new Response(font.body, { status: font.status, statusText: font.statusText, headers: fh });
    }

    // дашборд
    const isDash = url.pathname === "/dashboard" || url.pathname === "/dashboard/" || url.pathname === "/index.html";
    if (isDash && !rights.sites.includes("dashboard")) {
      ctx.waitUntil(audit(env, request, who, "dashboard", url.pathname, { denied: true }));
      return denyPage("Дашборд сорсинга · КВАНТ");
    }

    const resp = await env.ASSETS.fetch(isDash ? new Request(url.origin + "/index.html", { headers: request.headers }) : request);
    const headers = new Headers(resp.headers);
    headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    headers.set("Pragma", "no-cache");
    headers.set("Expires", "0");
    headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");

    const ctype = resp.headers.get("content-type") || "";
    if (ctype.includes("text/html")) {
      if (env.VISITS && request.headers.get("X-Poll") !== "1") ctx.waitUntil(logVisit(request, env));
      try {
        const buf = await resp.arrayBuffer();
        let text = new TextDecoder().decode(buf);
        const m = text.match(/gen=new Date\("([^"]+)"\)/);
        const genMs = m ? Date.parse(m[1]) : NaN;
        const stale = isNaN(genMs) || (Date.now() - genMs) > FRESH_MS;
        if (stale && env.GH_DISPATCH_TOKEN) ctx.waitUntil(triggerRebuild(env));
        text = cutDashboard(text, rights.tabs);
        text = text.replace('<div class="tabs">', portalBar(who, rights, env) + '<div class="tabs">');
        headers.delete("Content-Encoding");
        headers.delete("Content-Length");
        return new Response(text, { status: resp.status, statusText: resp.statusText, headers });
      } catch (e) {
        return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
      }
    }
    return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
  },
};


// Запись действия в журнал + разбор признаков. Вызывается через ctx.waitUntil,
// поэтому отдачу страницы не задерживает и её ошибками не ломает.
async function audit(env, request, who, site, path, opts = {}) {
  const done = await auditRecord(env, { request, email: who.email, site, path, kind: opts.kind, denied: opts.denied });
  if (!done) return;
  const signals = signalsFor(env, done.rec, done.counts, { firstEver: opts.firstEver });
  if (signals.length) await raiseAlerts(env, done.rec, signals);
}

// ── АДМИНКА: управление доступами ────────────────────────────────────────────
const ESC = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// seen === null означает, что хранилище не привязано: пустой список тогда не факт,
// а неизвестность, и панель обязана сказать об этом прямо.
function adminPage(acl, me, env, seen) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const roleIds = Object.keys(acl.roles);
  const noStore = seen === null;
  const facts = seen || {};

  // список = все, кому назначены права, плюс все, кто хоть раз заходил
  const blank = () => ({ role: acl.defaultRole, sites: [], tabs: [], note: "" });
  const merged = {};
  for (const [em, u] of Object.entries(acl.users)) merged[em] = { ...blank(), ...u };
  for (const [em, f] of Object.entries(facts)) merged[em] = { ...(merged[em] || blank()), ...f };
  const users = Object.entries(merged).sort((a, b) => {
    const d = String(b[1].last || "").localeCompare(String(a[1].last || ""));
    return d || a[0].localeCompare(b[0]);
  });
  const wasIn = users.filter(([, u]) => u.last).length;
  const opt = (sel) => roleIds.map((r) =>
    `<option value="${ESC(r)}"${r === sel ? " selected" : ""}>${ESC(acl.roles[r].name)}</option>`).join("");
  const fmt = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? "—" : d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  // строки людей
  const rows = users.map(([em, u]) => {
    const r = rightsFor(acl, em, env);
    const extra = (u.sites.length + u.tabs.length);
    return `<tr data-email="${ESC(em)}" data-search="${ESC(em + " " + (acl.roles[u.role] || {}).name + " " + (u.note || ""))}">
      <td class="em"><b>${ESC(em)}</b>${u.note ? `<div class="note">${ESC(u.note)}</div>` : ""}</td>
      <td><select class="role" data-email="${ESC(em)}">${opt(u.role)}</select></td>
      <td class="ct"><span class="pill${r.sites.length ? " on" : ""}">${r.sites.length} из ${SITE_IDS.length}</span></td>
      <td class="ct"><span class="pill${r.tabs.length ? " on" : ""}">${r.tabs.length} из ${TAB_IDS.length}</span></td>
      <td class="dim">${extra ? `+${extra} лично` : "—"}</td>
      <td class="dim">${u.last ? ESC(fmt(u.last)) : '<span style="color:#8b97a8">ни разу</span>'}</td>
      <td class="ct"><button class="lnk" data-open="${ESC(em)}">настроить</button></td>
    </tr>`;
  }).join("");

  // карточки ролей
  const roleCards = roleIds.map((id) => {
    const r = acl.roles[id];
    const chk = (arr, list, kind) => list.map((x) =>
      `<label class="chk"><input type="checkbox" data-role="${ESC(id)}" data-kind="${kind}" value="${ESC(x.id)}"${arr.includes(x.id) ? " checked" : ""}><span>${ESC(x.name)}</span></label>`).join("");
    // сайты — теми же разделами, что на портале: спецпроекты выдаются одним блоком
    const siteChk = (arr) => GROUPS.map((g) => {
      const own = SITES.filter((x) => x.group === g.id);
      if (!own.length) return "";
      return `<div class="sub">${ESC(g.name)} <button class="lnk all" data-role="${ESC(id)}" data-group="${ESC(g.id)}">все</button></div>` +
             `<div class="chks">${chk(arr, own, "site")}</div>`;
    }).join("");
    return `<div class="card role" data-role="${ESC(id)}">
      <div class="rh"><b>${ESC(r.name)}</b>${r.admin ? '<span class="pill adm">админ</span>' : ""}
        <span class="dim">${users.filter(([, u]) => u.role === id).length} чел.</span></div>
      <div class="grp"><div class="gt">Сайты</div>${siteChk(r.sites)}</div>
      <div class="grp"><div class="gt">Вкладки дашборда</div><div class="chks">${chk(r.tabs, TABS, "tab")}</div></div>
    </div>`;
  }).join("");

  const style = `
:root{--bg:#0e1116;--card:#151a22;--ln:#232a35;--ink:#e7ecf3;--dim:#8b97a8;--a:#5aa9ff;--ok:#3ecf8e;--wr:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 'IBM Plex Sans',-apple-system,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 70px}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.eyebrow{color:var(--a);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase}
h1{font-size:24px;margin:3px 0 0}h2{font-size:16px;margin:26px 0 10px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.05em}
.who{margin-left:auto;color:var(--dim);font-size:12.5px}.who a{color:var(--a);text-decoration:none;margin-left:10px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:11px;padding:15px 17px;margin-bottom:10px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--dim);font-size:11px;letter-spacing:.05em;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--ln);vertical-align:middle}
td.ct,th.ct{text-align:center}.em b{font-weight:600}.dim{color:var(--dim);font-size:12.5px}
.note{color:var(--dim);font-size:12px;margin-top:2px}
select,input[type=text]{background:#0f141b;border:1px solid var(--ln);color:var(--ink);border-radius:7px;padding:6px 9px;font:inherit}
select:focus,input:focus{outline:2px solid var(--a);outline-offset:1px}
.pill{display:inline-block;background:#1b2430;border:1px solid var(--ln);border-radius:999px;padding:2px 9px;font-size:11.5px;color:var(--dim)}
.pill.on{background:#123049;border-color:#1d4e79;color:#cfe6ff}.pill.adm{background:#2c2210;border-color:#5a4415;color:var(--wr);margin-left:8px}
.lnk{background:none;border:0;color:var(--a);cursor:pointer;font:inherit;padding:0}
.roles{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:10px}
.rh{display:flex;align-items:center;gap:9px;margin-bottom:10px}.rh .dim{margin-left:auto}
.grp{margin-top:9px}.gt{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}
.chks{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px}
.chk{display:flex;align-items:center;gap:7px;font-size:12.5px;cursor:pointer;padding:2px 0}
.chk input{accent-color:var(--a);width:15px;height:15px;flex:none}
.sub{display:flex;align-items:baseline;gap:8px;font-size:11.5px;color:#6b7787;margin:8px 0 3px}
.sub .all{font-size:11.5px}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.bar input{flex:1;min-width:220px}
.card.warn{border-color:#5a4415;background:#221a0d;color:#f0d9a8}
.card.warn code{background:#2c2210;padding:1px 5px;border-radius:4px}
.card.ok-note{color:var(--dim);font-size:13px}
.st{margin-left:auto;font-size:12.5px;color:var(--dim)}.st.ok{color:var(--ok)}.st.err{color:#ff6b6b}
dialog{background:var(--card);color:var(--ink);border:1px solid var(--ln);border-radius:12px;padding:0;max-width:620px;width:92vw}
dialog::backdrop{background:rgba(0,0,0,.6)}.dlg{padding:20px 22px}
.dlg h3{margin:0 0 4px;font-size:17px}.row{display:flex;gap:10px;margin-top:16px}
button.go{background:var(--a);color:#08101c;border:0;border-radius:8px;padding:9px 16px;font:600 13.5px inherit;cursor:pointer}
button.gh{background:#232a35;color:var(--ink)}
@media(max-width:720px){.chks{grid-template-columns:1fr}th.hide,td.hide{display:none}}`;

  const body = `<div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · портал</div><h1>Доступы</h1></div>
<div class="who">${ESC(me.email)}<a href="/admin/log">журнал</a><a href="/">портал</a><a href="https://${ESC(team)}/cdn-cgi/access/logout">выйти</a></div></div>

${noStore ? `<div class="card warn"><b>Учёт входов не ведётся.</b> К проекту Cloudflare Pages
  <code>kvant-sourcing-f122</code> не привязано хранилище KV, поэтому ни входы, ни правки прав
  не сохраняются. Привязка делается в Cloudflare: <i>Workers &amp; Pages → kvant-sourcing-f122 →
  Settings → Bindings → Add → KV namespace</i>, имя переменной <code>ACL</code>.</div>`
  : `<div class="card ok-note">Заходили: <b>${wasIn}</b> из ${users.length} в списке.
  Отметка ставится на любой странице портала и при обращении сайтов за правами, поэтому
  учитываются и те, кто заходит сразу на дашборд или другой сайт по закладке.</div>`}

<div class="card">
  <div class="bar">
    <input type="text" id="q" placeholder="Поиск по почте, роли, заметке…">
    <label class="dim">Роль для новых: <select id="defrole">${opt(acl.defaultRole)}</select></label>
    <span class="st" id="st">сохраняется сразу</span>
  </div>
  <div class="bar">
    <input type="text" id="newem" placeholder="почта сотрудника — завести заранее, не дожидаясь первого входа">
    <button class="go" id="newgo">Завести</button>
  </div>
  <table>
    <thead><tr><th>Сотрудник</th><th>Роль</th><th class="ct">Сайты</th><th class="ct">Вкладки</th>
      <th class="hide">Лично</th><th class="hide">Последний вход</th><th class="ct"></th></tr></thead>
    <tbody id="tb">${rows || '<tr><td colspan="7" class="dim" style="padding:16px">Пока никто не входил. Человек появится здесь после первого входа на портал.</td></tr>'}</tbody>
  </table>
</div>

<h2>Роли</h2>
<div class="dim" style="margin-bottom:10px;max-width:78ch">Роль — это набор прав. Меняете галочку в роли — меняется у всех, у кого эта роль. Правки сохраняются сразу.<br>
Две пары вкладок читают общий массив данных: <b>Реализация ↔ Поставщики</b> и <b>Пульс компании ↔ Когорты</b>. Выдача одной из пары в интерфейсе вторую не открывает, но её цифры остаются в исходном коде страницы. Разделить пары можно только правкой сборки дашборда.</div>
<div class="roles">${roleCards}</div>

<dialog id="dlg"><div class="dlg">
  <h3 id="dt"></h3><div class="dim" id="dr"></div>
  <div class="grp"><div class="gt">Дополнительно к роли — сайты</div><div class="chks" id="ds"></div></div>
  <div class="grp"><div class="gt">Дополнительно к роли — вкладки</div><div class="chks" id="dtb"></div></div>
  <div class="grp"><div class="gt">Заметка</div><input type="text" id="dn" style="width:100%" maxlength="200" placeholder="например: подрядчик, до конца проекта"></div>
  <div class="row"><button class="go" id="dsave">Сохранить</button><button class="go gh" id="dcancel">Отмена</button>
    <button class="go gh" id="ddrop" style="margin-left:auto;color:#ff8f8f">Снять с учёта</button></div>
</div></dialog>
</div>`;

  const data = JSON.stringify({ sites: SITES.map((s) => ({ id: s.id, name: s.name })),
                                tabs: TABS.map((t) => ({ id: t.id, name: t.name })),
                                users: acl.users, roles: acl.roles }).replace(/</g, "\\u003c");

  const script = `
const D = ${data};
const st = document.getElementById('st');
let t0;
function say(msg, cls){ st.textContent = msg; st.className = 'st ' + (cls||''); clearTimeout(t0); t0 = setTimeout(()=>{st.textContent='сохраняется сразу';st.className='st';}, 2500); }
async function post(op, payload){
  say('сохраняю…');
  try{
    const r = await fetch('/admin/api', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({op, ...payload})});
    const j = await r.json();
    if(!j.ok) throw new Error(j.error||'ошибка');
    say('сохранено', 'ok');
    return j;
  }catch(e){ say('не сохранилось: ' + e.message, 'err'); throw e; }
}
// роль человека
document.getElementById('tb').addEventListener('change', e => {
  const s = e.target.closest('select.role'); if(!s) return;
  post('user_role', {email: s.dataset.email, role: s.value}).then(()=>setTimeout(()=>location.reload(), 400));
});
// роль по умолчанию
document.getElementById('defrole').addEventListener('change', e => post('default_role', {role: e.target.value}));
// галочки ролей
document.querySelectorAll('.role input[type=checkbox]').forEach(cb => cb.addEventListener('change', () => {
  const card = cb.closest('.role'), role = card.dataset.role;
  const val = k => [...card.querySelectorAll('input[data-kind='+k+']:checked')].map(x=>x.value);
  post('role_rights', {role, sites: val('site'), tabs: val('tab')});
}));
// «все» в разделе сайтов
document.querySelectorAll('.role .all').forEach(b => b.addEventListener('click', () => {
  const card = b.closest('.role');
  const box = b.closest('.sub').nextElementSibling.querySelectorAll('input[type=checkbox]');
  const on = ![...box].every(x => x.checked);
  box.forEach(x => { x.checked = on; });
  const val = k => [...card.querySelectorAll('input[data-kind='+k+']:checked')].map(x=>x.value);
  post('role_rights', {role: card.dataset.role, sites: val('site'), tabs: val('tab')});
}));
// поиск
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('#tb tr[data-search]').forEach(tr => {
    tr.style.display = !q || tr.dataset.search.toLowerCase().includes(q) ? '' : 'none';
  });
});
// карточка человека
const dlg = document.getElementById('dlg');
let cur = null;
function boxes(el, list, have){
  el.innerHTML = list.map(x => '<label class="chk"><input type="checkbox" value="'+x.id+'"'+(have.includes(x.id)?' checked':'')+'><span>'+x.name+'</span></label>').join('');
}
document.getElementById('tb').addEventListener('click', e => {
  const b = e.target.closest('button[data-open]'); if(!b) return;
  cur = b.dataset.open; const u = D.users[cur] || {sites:[],tabs:[],note:'',role:''};
  document.getElementById('dt').textContent = cur;
  document.getElementById('dr').textContent = 'Роль: ' + ((D.roles[u.role]||{}).name || u.role) + ' — её права уже действуют. Ниже отмечается то, что даётся сверх роли, лично этому человеку.';
  boxes(document.getElementById('ds'), D.sites, u.sites||[]);
  boxes(document.getElementById('dtb'), D.tabs, u.tabs||[]);
  document.getElementById('dn').value = u.note || '';
  dlg.showModal();
});
document.getElementById('dcancel').addEventListener('click', () => dlg.close());
document.getElementById('newgo').addEventListener('click', async () => {
  const em = document.getElementById('newem').value.trim();
  if(!em) return;
  await post('user_add', {email: em, role: document.getElementById('defrole').value});
  setTimeout(()=>location.reload(), 400);
});
document.getElementById('ddrop').addEventListener('click', async () => {
  if(!confirm('Убрать ' + cur + ' из списка? Права вернутся к роли для новых, вход через Cloudflare Access останется.')) return;
  await post('user_drop', {email: cur});
  dlg.close(); setTimeout(()=>location.reload(), 400);
});
document.getElementById('dsave').addEventListener('click', async () => {
  const pick = id => [...document.getElementById(id).querySelectorAll('input:checked')].map(x=>x.value);
  await post('user_extra', {email: cur, sites: pick('ds'), tabs: pick('dtb'), note: document.getElementById('dn').value});
  dlg.close(); setTimeout(()=>location.reload(), 400);
});`;

  return new Response(`<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark">
<title>Доступы · КВАНТ</title>
<style>@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:400 600;font-display:swap;src:url(/fonts/ibm-plex-sans-var-cyr.woff2) format('woff2');unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:400 600;font-display:swap;src:url(/fonts/ibm-plex-sans-var-lat.woff2) format('woff2');unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+2000-206F,U+20AC,U+2122,U+2212}
${style}</style></head><body>${body}<script>${script}</script></body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
                 "X-Robots-Tag": "noindex, nofollow, noarchive", "Referrer-Policy": "no-referrer" } });
}

// приём правок из админки
async function adminApi(request, env, acl) {
  const json = (o, s = 200) => new Response(JSON.stringify(o),
    { status: s, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
  if (request.method !== "POST") return json({ ok: false, error: "post_only" }, 405);
  let b;
  try { b = await request.json(); } catch { return json({ ok: false, error: "bad_json" }, 400); }
  const pick = (arr, all) => (Array.isArray(arr) ? arr : []).filter((x) => all.includes(x));

  // Права хранятся отдельно от фактов входа, поэтому у вошедшего человека строки прав
  // может ещё не быть. Правка из панели её создаёт — иначе назначить роль было бы нельзя.
  const row = (em) => (acl.users[em] ||= { role: acl.defaultRole, sites: [], tabs: [], note: "" });

  if (b.op === "user_add") {
    const em = normEmail(b.email);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) return json({ ok: false, error: "плохая почта" }, 400);
    if (acl.users[em]) return json({ ok: false, error: "такой человек уже в списке" }, 409);
    acl.users[em] = { role: acl.roles[b.role] ? b.role : acl.defaultRole,
                      sites: [], tabs: [], note: "", first: "", last: "", seen: 0 };
  } else if (b.op === "user_drop") {
    const em = normEmail(b.email);
    if (!acl.users[em]) return json({ ok: false, error: "no_user" }, 404);
    delete acl.users[em];
  } else if (b.op === "user_role") {
    const em = normEmail(b.email);
    if (!em) return json({ ok: false, error: "no_user" }, 404);
    if (!acl.roles[b.role]) return json({ ok: false, error: "no_role" }, 400);
    row(em).role = b.role;
  } else if (b.op === "user_extra") {
    const em = normEmail(b.email);
    if (!em) return json({ ok: false, error: "no_user" }, 404);
    const u = row(em);
    u.sites = pick(b.sites, SITE_IDS);
    u.tabs = pick(b.tabs, TAB_IDS);
    u.note = String(b.note || "").slice(0, 200);
  } else if (b.op === "role_rights") {
    if (!acl.roles[b.role]) return json({ ok: false, error: "no_role" }, 400);
    acl.roles[b.role].sites = pick(b.sites, SITE_IDS);
    acl.roles[b.role].tabs = pick(b.tabs, TAB_IDS);
  } else if (b.op === "default_role") {
    if (!acl.roles[b.role]) return json({ ok: false, error: "no_role" }, 400);
    acl.defaultRole = b.role;
  } else {
    return json({ ok: false, error: "unknown_op" }, 400);
  }
  const saved = await saveAcl(env, acl);
  return json(saved ? { ok: true } : { ok: false, error: "нет хранилища: привяжите KV к проекту" }, saved ? 200 : 503);
}


// ЖУРНАЛ ДЕЙСТВИЙ. Только владельцу. Сведения о сотрудниках: в репозиторий не выгружаются
// и наружу не отдаются — читаются из KV и показываются здесь.
async function logPage(env, me, url) {
  const [recs, alerts] = await Promise.all([
    readLog(env, { limit: 400 }),
    readLog(env, { prefix: ALERT_PREFIX, limit: 60 }),
  ]);
  const noStore = recs === null;
  const rows = recs || [];
  const alr = alerts || [];

  const fmt = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return "—";
    return d.toLocaleString("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "short",
                                       hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  const KIND = { page: "просмотр", download: "выгрузка", admin: "правка прав", denied: "отказ" };
  const kindPill = (k) => `<span class="pill k-${ESC(k)}">${ESC(KIND[k] || k)}</span>`;
  const siteName = (id) => (SITES.find((x) => x.id === id) || {}).name || id || "портал";

  const body = rows.map((r) => `<tr data-search="${ESC([r.em, r.site, r.path, KIND[r.kind] || r.kind, r.geo, r.dev].join(" "))}">
    <td class="dim nw">${ESC(fmt(r.t))}</td>
    <td><b>${ESC(r.em)}</b><div class="dim">${ESC(r.dom)}</div></td>
    <td>${ESC(siteName(r.site))}<div class="dim mono">${ESC(r.path)}</div></td>
    <td class="ct">${kindPill(r.kind)}</td>
    <td class="dim nw">${ESC(r.geo)} · ${ESC(r.dev)}</td>
  </tr>`).join("");

  const alertRows = alr.map((a) => `<div class="al"><span class="dim nw">${ESC(fmt(a.t))}</span>
    <span>${ESC(a.text)}</span></div>`).join("");

  const dl = rows.filter((r) => r.kind === "download").length;
  const den = rows.filter((r) => r.kind === "denied").length;
  const people = new Set(rows.map((r) => r.em)).size;

  const style = `
:root{--bg:#0e1116;--card:#151a22;--ln:#232a35;--ink:#e7ecf3;--dim:#8b97a8;--a:#5aa9ff;--wr:#ffb020}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 'IBM Plex Sans',-apple-system,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 20px 70px}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:18px}
.eyebrow{color:var(--a);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase}
h1{font-size:24px;margin:3px 0 0}h2{font-size:12px;margin:24px 0 10px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.who{margin-left:auto;color:var(--dim);font-size:12.5px}.who a{color:var(--a);text-decoration:none;margin-left:10px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:11px;padding:15px 17px;margin-bottom:10px}
.card.warn{border-color:#5a4415;background:#221a0d;color:#f0d9a8}
.card.warn code{background:#2c2210;padding:1px 5px;border-radius:4px}
.sum{display:flex;gap:26px;flex-wrap:wrap}.sum div b{display:block;font-size:20px}.sum div span{color:var(--dim);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--dim);font-size:11px;letter-spacing:.05em;text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--ln);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--ln);vertical-align:top}
td.ct,th.ct{text-align:center}.dim{color:var(--dim);font-size:12px}.nw{white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;word-break:break-all}
.pill{display:inline-block;border-radius:999px;padding:2px 9px;font-size:11.5px;background:#1b2430;border:1px solid var(--ln);color:var(--dim)}
.pill.k-download{background:#123049;border-color:#1d4e79;color:#cfe6ff}
.pill.k-denied{background:#3a1418;border-color:#7d2731;color:#ffb3ba}
.pill.k-admin{background:#2c2210;border-color:#5a4415;color:var(--wr)}
.al{display:flex;gap:12px;padding:7px 0;border-bottom:1px solid var(--ln);font-size:13px}
.al:last-child{border-bottom:0}
input[type=text]{background:#0f141b;border:1px solid var(--ln);color:var(--ink);border-radius:7px;padding:7px 10px;font:inherit;width:100%}
input:focus{outline:2px solid var(--a);outline-offset:1px}
.note{color:var(--dim);font-size:12.5px;margin-top:18px;max-width:80ch}`;

  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark">
<title>Журнал действий · КВАНТ</title><style>${style}</style></head><body><div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · доступы</div><h1>Журнал действий</h1></div>
<div class="who">${ESC(me.email)}<a href="/admin">доступы</a><a href="/">портал</a></div></div>

${noStore ? `<div class="card warn"><b>Журнал не ведётся.</b> К проекту Pages не привязано хранилище KV
  (<i>Workers &amp; Pages → kvant-sourcing-f122 → Settings → Bindings → Add → KV namespace</i>,
  имя переменной <code>ACL</code>).</div>` : ""}

${alr.length ? `<h2>Признаки, о которых сообщено</h2><div class="card">${alertRows}</div>` : ""}

<div class="card sum">
  <div><b>${rows.length}</b><span>записей показано</span></div>
  <div><b>${people}</b><span>человек</span></div>
  <div><b>${dl}</b><span>выгрузок</span></div>
  <div><b>${den}</b><span>отказов</span></div>
</div>

<div class="card"><input type="text" id="q" placeholder="Поиск: почта, сайт, адрес, страна, устройство…"></div>
<div class="card" style="padding:0 4px">
<table><thead><tr><th>Время (МСК)</th><th>Кто</th><th>Что открыл</th><th class="ct">Действие</th><th>Откуда</th></tr></thead>
<tbody id="tb">${body || '<tr><td colspan="5" class="dim" style="padding:16px">Записей пока нет.</td></tr>'}</tbody></table>
</div>

<div class="note">Записи хранятся полгода в Cloudflare KV и никуда не выгружаются — репозиторий
публичный, сведения о сотрудниках в него не попадают. Просмотр доступен только владельцу.
Сотрудников о ведении журнала следует уведомить.</div>
</div>
<script>
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('#tb tr[data-search]').forEach(tr => {
    tr.style.display = !q || tr.dataset.search.toLowerCase().includes(q) ? '' : 'none';
  });
});
</script></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive", "Referrer-Policy": "no-referrer" } });
}

// Портал: плитки доступных человеку сайтов.
function portalPage(who, rights, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const mine = SITES.filter((s) => rights.sites.includes(s.id));
  // разделы: плашка показывается, только если в ней человеку что-то доступно
  const sections = GROUPS.map((g) => {
    const own = mine.filter((s) => s.group === g.id);
    if (!own.length) return "";
    const tiles = own.map((s) =>
      `<a class="tile" href="${esc(s.href)}"><div class="n">${esc(s.name)}</div><div class="d">${esc(s.note)}</div></a>`).join("");
    return `<section><h2>${esc(g.name)}<span>${esc(g.note)}</span></h2><div class="grid">${tiles}</div></section>`;
  }).join("");
  const empty = `<div class="card">Доступ к разделам пока не выдан. Обратитесь к владельцу — он назначает права на странице «Доступы».</div>`;
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark">
<title>Портал КВАНТ</title>
<style>
@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:400 600;font-display:swap;src:url(/fonts/ibm-plex-sans-var-cyr.woff2) format('woff2');unicode-range:U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116}
@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:400 600;font-display:swap;src:url(/fonts/ibm-plex-sans-var-lat.woff2) format('woff2');unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+2000-206F,U+20AC,U+2122,U+2212}
:root{--bg:#0e1116;--card:#151a22;--ln:#232a35;--ink:#e7ecf3;--dim:#8b97a8;--a:#5aa9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 'IBM Plex Sans',-apple-system,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:36px 20px 60px}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.eyebrow{color:var(--a);font-size:12px;letter-spacing:.1em;text-transform:uppercase}
h1{font-size:26px;margin:4px 0 0}.who{margin-left:auto;color:var(--dim);font-size:13px}.who a{color:var(--a);text-decoration:none;margin-left:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
section{margin-bottom:26px}
section h2{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;font-size:12px;font-weight:600;
letter-spacing:.09em;text-transform:uppercase;color:var(--dim);margin:0 0 10px;padding-bottom:7px;border-bottom:1px solid var(--ln)}
section h2 span{font-size:12px;font-weight:400;letter-spacing:0;text-transform:none;color:#6b7787}
.tile{display:block;background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:18px 20px;color:inherit;text-decoration:none;transition:border-color .15s}
.tile:hover,.tile:focus-visible{border-color:var(--a);outline:none}.tile .n{font-weight:600;font-size:17px;margin-bottom:6px}.tile .d{color:var(--dim);font-size:13px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:18px 20px;color:var(--dim)}
.note{color:var(--dim);font-size:12.5px;margin-top:22px;max-width:70ch}
</style></head><body><div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · единый вход</div><h1>Портал</h1></div>
<div class="who">${esc(who.email)}${rights.admin ? '<a href="/admin">доступы</a>' : ""}<a href="https://${esc(team)}/cdn-cgi/access/logout">выйти</a></div></div>
${mine.length ? sections : empty}
<div class="note">Вход по корпоративной почте, сессия действует месяц. Права на разделы назначает владелец.</div>
</div></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive", "Referrer-Policy": "no-referrer" } });
}

// плашка над дашбордом
function portalBar(who, rights, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  return `<div style="display:flex;gap:14px;align-items:center;justify-content:space-between;font:12px/1.4 'IBM Plex Sans',sans-serif;color:#8b97a8;padding:8px 14px 0">` +
    `<a href="/" style="color:#5aa9ff;text-decoration:none">← Портал КВАНТ</a>` +
    `<span>${esc(who.email)}${rights.admin ? ' · <a href="/admin" style="color:#5aa9ff;text-decoration:none">доступы</a>' : ""}` +
    ` · <a href="https://${esc(team)}/cdn-cgi/access/logout" style="color:#5aa9ff;text-decoration:none">выйти</a></span></div>`;
}

// Триггер пересборки через GitHub repository_dispatch, с дебаунсом через Cache API
// (не чаще раза в 15 мин на edge — чтобы пачка заходов в окно сборки не наплодила прогонов).
async function triggerRebuild(env) {
  try {
    const cache = caches.default;
    const marker = new Request("https://kvant-internal/rebuild-marker");
    if (await cache.match(marker)) return;                         // дебаунс активен
    await cache.put(marker, new Response("1", { headers: { "Cache-Control": "max-age=" + DEBOUNCE_MS } }));
    await fetch(`https://api.github.com/repos/${GH_REPO}/dispatches`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GH_DISPATCH_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "kvant-dashboard-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "rebuild" }),
    });
  } catch (e) { /* самообновление — best-effort, ошибки не мешают отдаче страницы */ }
}

// лог визита в KV: ключ v:{МСК-дата}:{IP} = число заходов за день (TTL 45 дней).
// Уникальные IP за день = число таких ключей; сумма значений = всего заходов.
async function logVisit(request, env) {
  try {
    const ip = request.headers.get("CF-Connecting-IP") || "0.0.0.0";
    const day = new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10); // МСК (UTC+3)
    const key = `v:${day}:${ip}`;
    const n = parseInt((await env.VISITS.get(key)) || "0", 10) + 1;
    await env.VISITS.put(key, String(n), { expirationTtl: 60 * 60 * 24 * 45 });
  } catch (e) { /* аналитика — best-effort */ }
}

// служебная страница отказа: без внешних ресурсов, светлая и тёмная тема
function denyPage(title) {
  const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title>
<style>:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--b:rgba(11,11,11,.10);--s1:#2a78d6}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--b:rgba(255,255,255,.10);--s1:#3987e5}}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.c{background:var(--surface);border:1px solid var(--b);border-left:4px solid var(--s1);border-radius:12px;
padding:22px 26px;max-width:560px}h1{font-size:19px;margin:0 0 10px}p{margin:9px 0;color:var(--ink2)}
a{color:var(--s1)}</style></head><body><div class="c"><h1>${title}</h1>
<p>Вход выполняется через портал КВАНТ по корпоративной почте.</p>
<p><a href="https://kvant-sourcing-f122.pages.dev/">Перейти к порталу</a></p></div></body></html>`;
  return new Response(html, {
    status: 403,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
               "X-Robots-Tag": "noindex, nofollow, noarchive" },
  });
}

const SITE = "sourcing";
// BEGIN accessOk
// Проверка входа через Cloudflare Access: подпись JWT (RS256) по открытым ключам команды,
// срок, издатель и, если задан CF_ACCESS_AUD, аудитория приложения. Возвращает {email, sub, exp}
// или null. Нужна на служебных адресах предпросмотра (<hash>.<проект>.pages.dev), куда
// периметр Access не распространяется: без действительного входа воркер ничего не отдаёт.
const CF_TEAM_DEFAULT = "small-bread-df2f.cloudflareaccess.com";
async function accessOk(request, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion") || readCookie(request, "CF_Authorization");
  if (!jwt) return null;
  const parts = jwt.split(".");
  if (parts.length !== 3) return null;
  let header, payload;
  try {
    header = JSON.parse(b64uText(parts[0]));
    payload = JSON.parse(b64uText(parts[1]));
  } catch { return null; }
  if (!header || header.alg !== "RS256" || !header.kid) return null;
  const now = Math.floor(Date.now() / 1000);
  if (typeof payload.exp !== "number" || payload.exp < now - 60) return null;
  if (payload.iss !== `https://${team}`) return null;
  const want = String((env && env.CF_ACCESS_AUD) || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (want.length) {
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!aud.some((a) => want.includes(a))) return null;
  }
  const keys = await accessCerts(team, env);
  const jwk = keys.find((k) => k && k.kid === header.kid);
  if (!jwk) return null;
  try {
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64uBytes(parts[2]),
      new TextEncoder().encode(parts[0] + "." + parts[1]));
    if (!ok) return null;
  } catch { return null; }
  const email = String(payload.email || "").trim().toLowerCase();
  return email ? { email, sub: String(payload.sub || ""), exp: payload.exp } : null;
}
// открытые ключи команды; кэш на edge на час. env.__certs — подстановка для тестов.
async function accessCerts(team, env) {
  if (env && env.__certs) return Array.isArray(env.__certs.keys) ? env.__certs.keys : [];
  const url = `https://${team}/cdn-cgi/access/certs`;
  let resp = null;
  try { if (typeof caches !== "undefined") resp = await caches.default.match(url); } catch { resp = null; }
  if (!resp) {
    resp = await fetch(url);
    if (!resp || !resp.ok) return [];
    try {
      const copy = resp.clone();
      const h = new Headers(copy.headers);
      h.set("Cache-Control", "max-age=3600");
      if (typeof caches !== "undefined") await caches.default.put(url, new Response(await copy.arrayBuffer(), { headers: h }));
    } catch { /* кэш — необязателен */ }
  }
  try { const j = await resp.json(); return Array.isArray(j.keys) ? j.keys : []; } catch { return []; }
}
function readCookie(request, name) {
  const raw = request.headers.get("Cookie") || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    if (part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}
function b64uBytes(s) {
  const b = String(s).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b + "=".repeat((4 - (b.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
function b64uText(s) { return new TextDecoder().decode(b64uBytes(s)); }
// END accessOk
