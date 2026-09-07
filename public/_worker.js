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

// Сайты компании. gt (справочник ГТУ) живёт по пути внутри проекта ЗИП,
// поэтому отдельным ресурсом не является — управляется вместе с zip.
const SITES = [
  { id: "dashboard", name: "Дашборд сорсинга", href: "/dashboard",
    note: "нагрузка, конверсия, реализация, поставщики — по данным Bitrix24" },
  { id: "zip", name: "База ЗИП и справочник ГТУ", href: "https://kvant-zip.pages.dev/",
    note: "позиции, ODM-аналоги, цены, таможня; внутри — справочник ГТУ и документы заказов" },
  { id: "gpu", name: "Библиотека ГПУ", href: "https://kvant-gpu.pages.dev/",
    note: "газопоршневые установки: Cummins, Caterpillar, INNIO — поставщики и цены" },
  { id: "ove", name: "ОВЭ-75", href: "https://kvant-ove.pages.dev/",
    note: "обжиг, выщелачивание, электроэкстракция — проект для Кольской ГМК" },
  { id: "gidromet", name: "Гидрометаллургия", href: "https://kvant-gidromet.pages.dev/",
    note: "заключение по переработке медно-золотого концентрата" },
  { id: "gok", name: "Базовый проект ГОКа", href: "https://kvant-gok.pages.dev/",
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
    version: 1,
    defaultRole: "employee",
    roles: {
      owner: { name: "Владелец", admin: true, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      head: { name: "Руководитель", admin: false, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      employee: { name: "Сотрудник", admin: false, sites: SITE_IDS.slice(), tabs: TAB_IDS.slice() },
      sourcing: { name: "Сорсинг", admin: false,
        sites: ["dashboard", "zip", "gpu"], tabs: ["sourcing", "contracts", "suppliers"] },
      kam: { name: "КАМ", admin: false,
        sites: ["dashboard", "zip"], tabs: ["company", "kam", "reps", "cohorts"] },
      engineer: { name: "Инженер", admin: false,
        sites: ["zip", "gpu", "ove", "gidromet", "gok"], tabs: [] },
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
  const roles = { ...d.roles };
  for (const [id, r] of Object.entries(raw.roles || {})) {
    if (!r || typeof r !== "object") continue;
    roles[id] = {
      name: String(r.name || id),
      admin: !!r.admin,
      sites: (Array.isArray(r.sites) ? r.sites : []).filter((x) => SITE_IDS.includes(x)),
      tabs: (Array.isArray(r.tabs) ? r.tabs : []).filter((x) => TAB_IDS.includes(x)),
    };
  }
  const defaultRole = roles[raw.defaultRole] ? raw.defaultRole : d.defaultRole;
  const users = {};
  for (const [em, u] of Object.entries(raw.users || {})) {
    if (!u || typeof u !== "object") continue;
    users[normEmail(em)] = {
      role: roles[u.role] ? u.role : defaultRole,
      sites: (Array.isArray(u.sites) ? u.sites : []).filter((x) => SITE_IDS.includes(x)),
      tabs: (Array.isArray(u.tabs) ? u.tabs : []).filter((x) => TAB_IDS.includes(x)),
      note: String(u.note || "").slice(0, 200),
      first: String(u.first || ""),
      last: String(u.last || ""),
      seen: Number(u.seen || 0),
    };
  }
  return { version: 1, defaultRole, roles, users };
}

async function aclStore(env) { return (env && (env.ACL || env.VISITS)) || null; }

async function loadAcl(env) {
  const kv = await aclStore(env);
  if (!kv) return defaultAcl();
  try {
    const raw = await kv.get(ACL_KEY, { type: "json" });
    return normalizeAcl(raw);
  } catch { return defaultAcl(); }
}

async function saveAcl(env, acl) {
  const kv = await aclStore(env);
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

// Отметка о входе: человек появляется в списке админки сам, заводить вручную не нужно.
async function touchUser(env, acl, email) {
  const em = normEmail(email);
  if (!em) return;
  const now = new Date().toISOString();
  const u = acl.users[em] || { role: acl.defaultRole, sites: [], tabs: [], note: "", first: now, seen: 0 };
  u.last = now;
  u.seen = (u.seen || 0) + 1;
  if (!u.first) u.first = now;
  acl.users[em] = u;
  try { await saveAcl(env, acl); } catch { /* учёт входов не должен ломать отдачу страницы */ }
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

export default {
  async fetch(request, env, ctx) {
    const who = await accessOk(request, env);
    if (!who) return denyPage("Портал КВАНТ");

    const url = new URL(request.url);
    const acl = await loadAcl(env);
    const rights = rightsFor(acl, who.email, env);

    // /api/rights — права для гейтов остальных сайтов: они шлют сюда JWT вошедшего,
    // мы его проверяем тем же помощником и отвечаем набором прав. Общих секретов не нужно.
    if (url.pathname === "/api/rights") {
      return new Response(JSON.stringify({ email: rights.email, sites: rights.sites, tabs: rights.tabs, admin: rights.admin }),
        { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
    }

    // админка — только владельцу
    if (url.pathname === "/admin" || url.pathname === "/admin/") {
      if (!rights.admin) return denyPage("Доступы · КВАНТ");
      return adminPage(acl, who, env);
    }
    if (url.pathname === "/admin/api") {
      if (!rights.admin) return new Response(JSON.stringify({ ok: false, error: "forbidden" }),
        { status: 403, headers: { "Content-Type": "application/json" } });
      return adminApi(request, env, acl);
    }

    // портал — корень
    if (url.pathname === "/" || url.pathname === "/portal" || url.pathname === "/portal/") {
      ctx.waitUntil(touchUser(env, acl, who.email));
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
    if (isDash && !rights.sites.includes("dashboard")) return denyPage("Дашборд сорсинга · КВАНТ");

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


// ── АДМИНКА: управление доступами ────────────────────────────────────────────
const ESC = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function adminPage(acl, me, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const roleIds = Object.keys(acl.roles);
  const users = Object.entries(acl.users).sort((a, b) => String(b[1].last || "").localeCompare(String(a[1].last || "")));
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
      <td class="dim">${ESC(fmt(u.last))}</td>
      <td class="ct"><button class="lnk" data-open="${ESC(em)}">настроить</button></td>
    </tr>`;
  }).join("");

  // карточки ролей
  const roleCards = roleIds.map((id) => {
    const r = acl.roles[id];
    const chk = (arr, list, kind) => list.map((x) =>
      `<label class="chk"><input type="checkbox" data-role="${ESC(id)}" data-kind="${kind}" value="${ESC(x.id)}"${arr.includes(x.id) ? " checked" : ""}><span>${ESC(x.name)}</span></label>`).join("");
    return `<div class="card role" data-role="${ESC(id)}">
      <div class="rh"><b>${ESC(r.name)}</b>${r.admin ? '<span class="pill adm">админ</span>' : ""}
        <span class="dim">${users.filter(([, u]) => u.role === id).length} чел.</span></div>
      <div class="grp"><div class="gt">Сайты</div><div class="chks">${chk(r.sites, SITES, "site")}</div></div>
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
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.bar input{flex:1;min-width:220px}
.st{margin-left:auto;font-size:12.5px;color:var(--dim)}.st.ok{color:var(--ok)}.st.err{color:#ff6b6b}
dialog{background:var(--card);color:var(--ink);border:1px solid var(--ln);border-radius:12px;padding:0;max-width:620px;width:92vw}
dialog::backdrop{background:rgba(0,0,0,.6)}.dlg{padding:20px 22px}
.dlg h3{margin:0 0 4px;font-size:17px}.row{display:flex;gap:10px;margin-top:16px}
button.go{background:var(--a);color:#08101c;border:0;border-radius:8px;padding:9px 16px;font:600 13.5px inherit;cursor:pointer}
button.gh{background:#232a35;color:var(--ink)}
@media(max-width:720px){.chks{grid-template-columns:1fr}th.hide,td.hide{display:none}}`;

  const body = `<div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · портал</div><h1>Доступы</h1></div>
<div class="who">${ESC(me.email)}<a href="/">портал</a><a href="https://${ESC(team)}/cdn-cgi/access/logout">выйти</a></div></div>

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
    if (!acl.users[em]) return json({ ok: false, error: "no_user" }, 404);
    if (!acl.roles[b.role]) return json({ ok: false, error: "no_role" }, 400);
    acl.users[em].role = b.role;
  } else if (b.op === "user_extra") {
    const em = normEmail(b.email);
    if (!acl.users[em]) return json({ ok: false, error: "no_user" }, 404);
    acl.users[em].sites = pick(b.sites, SITE_IDS);
    acl.users[em].tabs = pick(b.tabs, TAB_IDS);
    acl.users[em].note = String(b.note || "").slice(0, 200);
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


// Портал: плитки доступных человеку сайтов.
function portalPage(who, rights, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const mine = SITES.filter((s) => rights.sites.includes(s.id));
  const tiles = mine.map((s) =>
    `<a class="tile" href="${esc(s.href)}"><div class="n">${esc(s.name)}</div><div class="d">${esc(s.note)}</div></a>`).join("");
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
.tile{display:block;background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:18px 20px;color:inherit;text-decoration:none;transition:border-color .15s}
.tile:hover,.tile:focus-visible{border-color:var(--a);outline:none}.tile .n{font-weight:600;font-size:17px;margin-bottom:6px}.tile .d{color:var(--dim);font-size:13px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:18px 20px;color:var(--dim)}
.note{color:var(--dim);font-size:12.5px;margin-top:22px;max-width:70ch}
</style></head><body><div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · единый вход</div><h1>Портал</h1></div>
<div class="who">${esc(who.email)}${rights.admin ? '<a href="/admin">доступы</a>' : ""}<a href="https://${esc(team)}/cdn-cgi/access/logout">выйти</a></div></div>
${mine.length ? `<div class="grid">${tiles}</div>` : empty}
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
