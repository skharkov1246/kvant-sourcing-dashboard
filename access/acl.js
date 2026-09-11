// ПРАВА ДОСТУПА — канонический экземпляр. Блок между маркерами BEGIN/END aclCore
// вшит в public/_worker.js; совпадение копий проверяет access/test/acl.test.mjs.
//
// Периметр (кто вообще войдёт) держит Cloudflare Access: политика по домену почты.
// Этот модуль решает следующий вопрос — ЧТО именно человек видит внутри:
//   • какие сайты компании показывать на портале и пускать в них,
//   • какие вкладки дашборда отдавать (данные чужих вкладок вырезаются на сервере).
//
// Хранилище — Cloudflare KV, один документ acl:v1. Читается на каждом запросе
// (KV кэшируется на границе сети), пишется только из админки.
//
// Модель намеренно простая, чтобы выдача доступа не требовала размышлений:
//   роль = набор прав; человеку назначается роль; точечные добавки — исключением.
// Роль «owner» несёт признак админа; список админов дополнительно задаётся
// переменной ADMIN_EMAILS (на случай потери доступа к хранилищу).

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
  { id: "knowledge", group: "lib", name: "Библиотека оборудования и знаний", href: "/library",
    note: "узлы и детали, изготовители, трейдеры, аналоги, цены и проверенные источники" },
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
        sites: ["dashboard", "zip", "gt", "gpu", "knowledge"], tabs: ["sourcing", "contracts", "suppliers"] },
      kam: { name: "КАМ", admin: false,
        sites: ["dashboard", "zip", "gt", "knowledge"], tabs: ["company", "kam", "reps", "cohorts"] },
      engineer: { name: "Инженер", admin: false,
        sites: ["zip", "gt", "gpu", "knowledge", "ove", "gidromet", "gok"], tabs: [] },
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

async function loadAcl(env, { strict = false } = {}) {
  const kv = aclStore(env);
  if (!kv) {
    if (strict) throw new Error("acl_unavailable");
    return defaultAcl();
  }
  try {
    const raw = await kv.get(ACL_KEY, { type: "json" });
    if (strict && raw !== null && (!raw || typeof raw !== "object" || Array.isArray(raw) ||
        !raw.roles || typeof raw.roles !== "object" || Array.isArray(raw.roles))) throw new Error("acl_invalid");
    return normalizeAcl(raw);
  } catch (error) {
    if (strict) throw error;
    return defaultAcl();
  }
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

export {
  SITES, GROUPS, TABS, SITE_IDS, TAB_IDS, PAYLOADS, ACL_KEY, ACL_VERSION,
  defaultAcl, normalizeAcl, normEmail, aclStore, loadAcl, saveAcl, rightsFor,
  touchUser, loadSeen, cutDashboard,
};
