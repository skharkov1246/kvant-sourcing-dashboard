// ЯДРО ГЕЙТА ДОСТУПОВ «по заявке» — общий код для Cloudflare-воркера и локального
// тестового контура (access/devserver.mjs). Ничего специфичного для Workers здесь нет:
// используются только WebCrypto, fetch и переданный env.
//
// СЦЕНАРИЙ (как просил владелец):
//   1. Человек открывает сайт → видит форму «Запросить доступ» и отмечает ВКЛАДКИ,
//      которые ему нужны, с обоснованием.
//   2. Владельцу на почту падает письмо со сводкой заявки и ссылкой-подтверждением.
//   3. Владелец по ссылке открывает страницу решения: может снять/добавить галочки
//      вкладок, выбрать срок доступа и нажать «Выдать доступ» или «Отказать».
//   4. Заявителю уходит письмо с одноразовой ссылкой входа (magic link) → в браузере
//      ставится подписанная сессионная кука на 30 дней.
//   5. При каждом заходе ACL перечитывается из KV, а HTML дашборда режется на лету:
//      чужие вкладки не отрисовываются и — главное — их ДАННЫЕ не попадают в страницу.
//
// Пароль/логин Basic Auth продолжают работать как «рут»: полный доступ + админка.
// Это страховка от блокировки владельца и путь плавной миграции с текущего гейта.

// ---------------------------------------------------------------------------
// ВКЛАДКИ И ИХ ДАННЫЕ
// ---------------------------------------------------------------------------
// pay — какие window.__X__ нужны вкладке. Если ни одна разрешённая вкладка не просит
// payload, он заменяется на null ещё на сервере → данных нет даже в исходном коде.
// share — предупреждение в UI: вкладка делит массив данных с другой вкладкой,
// поэтому выдача одной технически открывает данные второй (см. README, «Точность ACL»).
export const TABS = [
  { id: "sourcing",  title: "Сорсинг",           pay: ["DATA", "INSIGHTS"] },
  { id: "company",   title: "Пульс компании",    pay: ["COMPANY"],   share: "cohorts" },
  { id: "kam",       title: "КАМы",              pay: ["KAM"] },
  { id: "eng",       title: "Инжиниринг",        pay: ["ENG"] },
  { id: "prod",      title: "Продукт-оунеры",    pay: ["PRODUCT"] },
  { id: "reps",      title: "Коммерсанты",       pay: ["REPS"] },
  { id: "contracts", title: "Реализация",        pay: ["CONTRACTS"], share: "suppliers" },
  { id: "suppliers", title: "Поставщики",        pay: ["CONTRACTS"], share: "contracts" },
  { id: "cohorts",   title: "Когорты",           pay: ["COMPANY"],   share: "company" },
  { id: "advisor",   title: "Советы знатока",    pay: ["ADVISOR"] },
];
export const TAB_IDS = TABS.map((t) => t.id);
const ALL_PAYLOADS = ["DATA", "INSIGHTS", "COMPANY", "KAM", "ENG", "PRODUCT", "CONTRACTS", "REPS", "ADVISOR"];
const TITLE = Object.fromEntries(TABS.map((t) => [t.id, t.title]));

const SESSION_DAYS = 30;       // срок сессионной куки
const MAGIC_MIN = 60 * 24;     // срок ссылки входа — сутки
const REVIEW_DAYS = 14;        // срок ссылки подтверждения для владельца
const RL_MAX = 8;              // не больше 8 заявок/логинов с одного IP в час

// ---------------------------------------------------------------------------
// МЕЛОЧИ
// ---------------------------------------------------------------------------
const enc = new TextEncoder();
const now = () => Date.now();
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const normEmail = (s) => String(s || "").trim().toLowerCase();
const isEmail = (s) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(normEmail(s));
const uid = () => [...crypto.getRandomValues(new Uint8Array(9))]
  .map((b) => b.toString(36).padStart(2, "0")).join("").slice(0, 14);

function b64url(bytes) {
  let s = "";
  for (const b of new Uint8Array(bytes)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function unb64url(str) {
  const s = String(str).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(s + "=".repeat((4 - (s.length % 4)) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
function eqConst(a, b) {          // сравнение за постоянное время
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

// ---------------------------------------------------------------------------
// ПОДПИСАННЫЕ ТОКЕНЫ (HMAC-SHA256, без внешних библиотек)
// ---------------------------------------------------------------------------
async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}
async function sign(env, payload) {
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const sig = b64url(await crypto.subtle.sign("HMAC", await hmacKey(signSecret(env)), enc.encode(body)));
  return `${body}.${sig}`;
}
async function verify(env, token) {
  const [body, sig] = String(token || "").split(".");
  if (!body || !sig) return null;
  const ok = await crypto.subtle.verify("HMAC", await hmacKey(signSecret(env)), unb64url(sig), enc.encode(body));
  if (!ok) return null;
  let p;
  try { p = JSON.parse(new TextDecoder().decode(unb64url(body))); } catch { return null; }
  if (!p || (p.exp && p.exp < now())) return null;
  return p;
}
// Ключ подписи. В тестовом контуре допускаем дефолт, в проде без секрета гейт не стартует.
function signSecret(env) {
  return env.SIGN_KEY || env.BASIC_AUTH_PASS || (isTest(env) ? "test-contour-key" : "");
}
const isTest = (env) => String(env.CONTOUR || "").toLowerCase() === "test";

// ---------------------------------------------------------------------------
// ХРАНИЛИЩЕ (KV): u:<email> — пользователь, r:<id> — заявка, m:<id> — тестовый outbox
// ---------------------------------------------------------------------------
const kv = (env) => env.ACL;
async function getUser(env, email) {
  const raw = await kv(env).get("u:" + normEmail(email));
  return raw ? JSON.parse(raw) : null;
}
async function putUser(env, u) {
  u.email = normEmail(u.email);
  await kv(env).put("u:" + u.email, JSON.stringify(u));
  return u;
}
async function listAll(env, prefix) {
  const out = [];
  let cursor;
  do {
    const page = await kv(env).list({ prefix, cursor });
    for (const k of page.keys) {
      const raw = await kv(env).get(k.name);
      if (raw) out.push(JSON.parse(raw));
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  return out;
}
// действующий ACL пользователя: снят/просрочен → пусто
function userTabs(u) {
  if (!u || u.revoked) return [];
  if (u.exp && u.exp < now()) return [];
  if (u.admin) return TAB_IDS.slice();
  return (u.tabs || []).filter((t) => TAB_IDS.includes(t));
}

async function rateOk(env, ip) {
  const key = `rl:${ip}:${Math.floor(now() / 3600000)}`;
  const n = parseInt((await kv(env).get(key)) || "0", 10) + 1;
  await kv(env).put(key, String(n), { expirationTtl: 7200 });
  return n <= RL_MAX;
}

// ---------------------------------------------------------------------------
// ПОЧТА. Провайдер выбирается env.MAIL_PROVIDER:
//   resend — боевой (нужны RESEND_API_KEY и MAIL_FROM)
//   log    — тестовый контур: письмо кладётся в KV-«ящик» /access/outbox и в консоль
// ---------------------------------------------------------------------------
async function sendMail(env, { to, subject, html, text }) {
  const provider = env.MAIL_PROVIDER || (isTest(env) ? "log" : "resend");
  const rec = { id: uid(), at: new Date().toISOString(), to, subject, html, text: text || stripTags(html), provider };
  if (provider === "resend" && env.RESEND_API_KEY) {
    try {
      const r = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({ from: env.MAIL_FROM || "dashboard@kvant.local", to: [to], subject, html }),
      });
      rec.ok = r.ok;
      if (!r.ok) rec.error = `resend ${r.status}`;
    } catch (e) { rec.ok = false; rec.error = String(e && e.message || e); }
  } else {
    rec.ok = true;
    rec.note = "провайдер не настроен — письмо только в ящике контура";
  }
  try {
    await kv(env).put("m:" + rec.at + ":" + rec.id, JSON.stringify(rec), { expirationTtl: 60 * 60 * 24 * 14 });
  } catch { /* ящик — вспомогательный, ошибки не ломают поток */ }
  if (isTest(env) && env.MAIL_LOG !== "0") {
    console.log(`\n[mail ${rec.ok ? "ok" : "FAIL"}] -> ${to}\n   ${subject}\n   ${linksOf(html).join("\n   ")}\n`);
  }
  return rec;
}
const stripTags = (h) => String(h || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const linksOf = (h) => [...String(h || "").matchAll(/https?:\/\/[^"'<\s]+/g)].map((m) => m[0]);

// ---------------------------------------------------------------------------
// ОФОРМЛЕНИЕ СЛУЖЕБНЫХ СТРАНИЦ (тёмная тема дашборда, самодостаточный CSS)
// ---------------------------------------------------------------------------
const CSS = `
:root{--bg:#0e1116;--card:#151a22;--ln:#232a35;--ink:#e7ecf3;--dim:#8b97a8;--a:#5aa9ff;--ok:#3ecf8e;--warn:#ff6b6b;--amb:#ffb020}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 'IBM Plex Sans',-apple-system,Segoe UI,Roboto,sans-serif;padding:28px 16px}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:22px;margin:0 0 6px}h2{font-size:15px;margin:22px 0 8px;color:var(--dim);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.eyebrow{color:var(--a);font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:12px;padding:18px;margin:14px 0}
label{display:block;font-size:12px;color:var(--dim);margin:12px 0 4px}
input[type=text],input[type=email],textarea,select{width:100%;background:#0f141b;border:1px solid var(--ln);color:var(--ink);border-radius:8px;padding:10px 12px;font:inherit}
textarea{min-height:76px;resize:vertical}
.tabsel{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;margin-top:6px}
.tabsel label{display:flex;align-items:center;gap:9px;background:#0f141b;border:1px solid var(--ln);border-radius:8px;padding:10px 12px;margin:0;color:var(--ink);font-size:14px;cursor:pointer}
.tabsel label:hover{border-color:var(--a)}
.tabsel input{accent-color:var(--a);width:16px;height:16px}
button,.btn{background:var(--a);color:#08101c;border:0;border-radius:8px;padding:11px 18px;font:600 14px/1 inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.gh{background:#232a35;color:var(--ink)}
button.dg{background:#3a2226;color:#ffb3b3}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:16px}
.note{color:var(--dim);font-size:12.5px;margin-top:8px}
.warn{color:var(--amb);font-size:12.5px;margin-top:8px}
.ok{color:var(--ok)} .bad{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--ln);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
.pill{display:inline-block;background:#1b2430;border:1px solid var(--ln);border-radius:999px;padding:2px 9px;font-size:11.5px;margin:2px 3px 2px 0}
.pill.on{background:#123049;border-color:#1d4e79;color:#cfe6ff}
.banner{background:#1b2430;border:1px solid var(--ln);border-radius:10px;padding:12px 14px;margin-bottom:14px;font-size:13.5px}
a{color:var(--a)}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
`;
function page(title, body, { contour } = {}) {
  return new Response(
    `<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">` +
    `<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">` +
    `<title>${esc(title)} · КВАНТ</title><style>${CSS}</style></head><body><div class="wrap">` +
    (contour ? `<div class="banner">🧪 <b>Тестовый контур</b> — данные и письма ненастоящие, на прод ничего не влияет.</div>` : "") +
    body + `</div></body></html>`,
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
  );
}
const tabChecks = (checked, name = "tabs") => `<div class="tabsel">` + TABS.map((t) =>
  `<label><input type="checkbox" name="${name}" value="${t.id}"${checked.includes(t.id) ? " checked" : ""}>` +
  `<span>${esc(t.title)}</span></label>`).join("") + `</div>` +
  `<div class="note">«Реализация» и «Поставщики» строятся из одного массива данных — как и «Пульс компании» с «Когортами». ` +
  `Выдача одной вкладки из пары технически открывает данные второй в исходном коде страницы.</div>`;

// ---------------------------------------------------------------------------
// РЕЗКА ДАШБОРДА ПОД ACL
// ---------------------------------------------------------------------------
// Пустышка вместо вырезанного массива данных: любое обращение возвращает такую же
// пустышку, вызов — тоже, а при выводе в строку/число она даёт ''/0. Так шаблон,
// написанный в расчёте на всегда присутствующие данные, не роняет страницу.
const NO_DATA_STUB = `<script>function __kvNoData(){return new Proxy(function(){},{` +
  `get:function(t,p){` +
  `if(p===Symbol.toPrimitive)return function(){return ''};` +
  `if(p===Symbol.iterator)return function*(){};` +
  `if(p==='length')return 0;` +
  `if(p==='toJSON'||p==='then'||p===Symbol.toStringTag)return undefined;` +
  `return __kvNoData()},` +
  `apply:function(){return __kvNoData()},` +
  `construct:function(){return __kvNoData()}})}</script>`;

export function applyAcl(html, tabs, who) {
  const allow = new Set(tabs);
  // 1) кнопки чужих вкладок вырезаем
  html = html.replace(/<button class="tab[^"]*" data-tab="([a-z]+)"[^>]*>[\s\S]*?<\/button>/g,
    (m, id) => (allow.has(id) ? m : ""));
  // 2) пустые панели чужих вкладок вырезаем (они всё равно наполняются из JS)
  for (const t of TAB_IDS) {
    if (allow.has(t) || t === "sourcing") continue;
    html = html.replace(new RegExp(`<div id="tab-${t}" hidden></div>`), "");
  }
  // 3) данные: payload остаётся, только если его просит хотя бы одна разрешённая вкладка.
  //    Вместо null подставляем «пустышку» __kvNoData(): часть кода шаблона читает данные
  //    сразу при загрузке (шапка «Сорсинга», датчик свежести), и на голом null он падает —
  //    а вместе с ним и обработчики вкладок. Пустышка отдаёт себя на любое обращение и
  //    сводится к пустой строке/нулю при выводе, поэтому страница остаётся живой без данных.
  const need = new Set();
  for (const t of TABS) if (allow.has(t.id)) for (const p of t.pay) need.add(p);
  const cut = ALL_PAYLOADS.filter((p) => !need.has(p));
  for (const p of cut) {
    html = html.replace(new RegExp(`window\\.__${p}__ *= *[\\s\\S]*?;\\n`), `window.__${p}__ = __kvNoData();\n`);
  }
  if (cut.length) html = html.replace("<body>", "<body>\n" + NO_DATA_STUB);
  // 4) «Сорсинг» — единственная вкладка со статической разметкой: если её нет, прячем панель
  //    и после загрузки открываем первую доступную (клик запускает штатный ленивый рендер)
  let tail = "";
  if (!allow.has("sourcing")) {
    html = html.replace('<div id="tab-sourcing">', '<div id="tab-sourcing" hidden>');
    tail += `<script>(function(){var b=document.querySelector('.tabs .tab');if(b)b.click();})();</script>`;
  }
  // 5) плашка «кто вошёл» + выход
  const bar = `<div style="display:flex;gap:10px;align-items:center;justify-content:flex-end;` +
    `font:12px/1.4 'IBM Plex Sans',sans-serif;color:#8b97a8;padding:6px 14px 0">` +
    `<span>${esc(who.email)}${who.admin ? " · админ" : ""} · доступ: ${tabs.length} из ${TAB_IDS.length} вкладок</span>` +
    (who.admin ? `<a href="/admin" style="color:#5aa9ff;text-decoration:none">админка</a>` : "") +
    `<a href="/access/logout" style="color:#5aa9ff;text-decoration:none">выйти</a></div>`;
  html = html.replace('<div class="tabs">', bar + '<div class="tabs">');
  if (tail) html = html.replace("</body>", tail + "</body>");
  return html;
}

// ---------------------------------------------------------------------------
// СЕССИЯ
// ---------------------------------------------------------------------------
function cookieVal(request, name) {
  const raw = request.headers.get("Cookie") || "";
  for (const part of raw.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return decodeURIComponent(v.join("="));
  }
  return null;
}
function setCookie(name, value, maxAge, secure) {
  return `${name}=${encodeURIComponent(value)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}` + (secure ? "; Secure" : "");
}

// Кто пришёл: {email, tabs, admin, via}. null — не аутентифицирован.
async function whoami(request, env) {
  // «рут» по Basic Auth — полный доступ, чтобы владелец не заблокировал сам себя
  const got = request.headers.get("Authorization") || "";
  if (env.BASIC_AUTH_PASS && got.startsWith("Basic ")) {
    const want = "Basic " + btoa(`${env.BASIC_AUTH_USER || "kvant"}:${env.BASIC_AUTH_PASS}`);
    if (eqConst(got, want)) {
      return { email: normEmail(env.OWNER_EMAIL) || "root", tabs: TAB_IDS.slice(), admin: true, via: "basic" };
    }
  }
  const p = await verify(env, cookieVal(request, "kv_sid"));
  if (!p || p.k !== "sid") return null;
  const u = await getUser(env, p.email);
  const tabs = userTabs(u);
  if (!u || !tabs.length) return null;                 // отзыв доступа действует немедленно
  return { email: u.email, name: u.name, tabs, admin: !!u.admin, via: "session" };
}

// ---------------------------------------------------------------------------
// ГЛАВНЫЙ ОБРАБОТЧИК
// ---------------------------------------------------------------------------
export async function handle(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname;
  const base = (env.SITE_URL || url.origin).replace(/\/+$/, "");
  const secure = url.protocol === "https:";
  const test = isTest(env);
  const ip = request.headers.get("CF-Connecting-IP") || request.headers.get("X-Forwarded-For") || "0.0.0.0";

  if (!kv(env)) return new Response("Гейт не настроен: не привязано KV-хранилище ACL.", { status: 503 });
  if (!signSecret(env)) return new Response("Гейт не настроен: задайте секрет SIGN_KEY.", { status: 503 });

  const me = await whoami(request, env);

  // ---------- публичные маршруты заявки/входа ----------
  if (path === "/access" || path === "/access/") return pageRequest(env, url, test);

  if (path === "/access/request" && request.method === "POST") {
    const f = await request.formData();
    const email = normEmail(f.get("email"));
    const tabs = f.getAll("tabs").map(String).filter((t) => TAB_IDS.includes(t));
    if (!isEmail(email)) return pageRequest(env, url, test, { err: "Укажите рабочую почту." });
    if (!tabs.length) return pageRequest(env, url, test, { err: "Отметьте хотя бы одну вкладку — заявка без вкладок бессмысленна." });
    if (!(await rateOk(env, ip))) return page("Слишком часто", `<h1>Слишком много заявок</h1><div class="note">Попробуйте через час.</div>`, { contour: test });
    const req = {
      id: uid(), email, name: String(f.get("name") || "").trim(),
      org: String(f.get("org") || "").trim(), reason: String(f.get("reason") || "").trim(),
      tabs, status: "pending", createdAt: new Date().toISOString(), ip,
    };
    await kv(env).put("r:" + req.id, JSON.stringify(req));
    const token = await sign(env, { k: "rev", id: req.id, exp: now() + REVIEW_DAYS * 864e5 });
    const link = `${base}/access/review?t=${token}`;
    await sendMail(env, {
      to: env.OWNER_EMAIL || "skharkov@gmail.com",
      subject: `Запрос доступа к дашборду: ${req.name || req.email}`,
      html: mailOwner(req, link),
    });
    await sendMail(env, {
      to: req.email, subject: "Заявка на доступ к дашборду КВАНТ принята",
      html: `<p>Заявка принята и отправлена на подтверждение владельцу.</p>` +
        `<p>Запрошенные вкладки: ${req.tabs.map((t) => TITLE[t]).join(", ")}.</p>` +
        `<p>Когда доступ одобрят, вам придёт письмо со ссылкой входа.</p>`,
    });
    return page("Заявка отправлена", `<div class="eyebrow">Квант · дашборд сорсинга</div>` +
      `<h1>Заявка отправлена</h1><div class="card"><div>Владелец получит письмо и подтвердит доступ.` +
      ` Ссылка входа придёт на <b>${esc(req.email)}</b>.</div>` +
      `<div class="note">Запрошено: ${req.tabs.map((t) => `<span class="pill on">${esc(TITLE[t])}</span>`).join("")}</div></div>` +
      (test ? `<div class="note">Тестовый контур: письма смотрите в <a href="/access/outbox">ящике контура</a>.</div>` : ""),
      { contour: test });
  }

  if (path === "/access/login" && request.method === "POST") {
    const f = await request.formData();
    const email = normEmail(f.get("email"));
    const done = page("Проверьте почту", `<h1>Проверьте почту</h1><div class="card">Если у этой почты есть доступ, ` +
      `мы отправили на неё ссылку входа. Ссылка действует сутки и срабатывает один раз.</div>` +
      (test ? `<div class="note">Тестовый контур: письмо в <a href="/access/outbox">ящике контура</a>.</div>` : ""),
      { contour: test });
    if (!isEmail(email) || !(await rateOk(env, ip))) return done;
    let u = await getUser(env, email);
    // владелец заводится сам: без этого некому было бы одобрять первые заявки
    if (!u && env.OWNER_EMAIL && email === normEmail(env.OWNER_EMAIL)) {
      u = await putUser(env, { email, name: "Владелец", admin: true, tabs: TAB_IDS.slice(),
        exp: null, createdAt: new Date().toISOString() });
    }
    if (u && userTabs(u).length) await mailMagic(env, base, u);
    return done;                                        // одинаковый ответ — не подсказываем, кто есть в системе
  }

  // Аварийный вход владельца по логину/паролю: браузер не пришлёт Basic Auth,
  // пока не получит 401 с вызовом, — здесь мы его и выдаём.
  if (path === "/access/basic") {
    if (me && me.admin) {
      // выдаём нормальную сессию: браузер шлёт Basic Auth не на все пути,
      // и без куки владелец «проваливался» бы обратно на страницу входа
      const email = normEmail(env.OWNER_EMAIL) || "root@local";
      const prev = await getUser(env, email);
      const u = await putUser(env, { ...(prev || {}), email, name: (prev && prev.name) || "Владелец",
        admin: true, tabs: TAB_IDS.slice(), exp: null, revoked: false,
        createdAt: (prev && prev.createdAt) || new Date().toISOString() });
      const sid = await sign(env, { k: "sid", email: u.email, exp: now() + SESSION_DAYS * 864e5 });
      return new Response(null, { status: 302, headers: {
        Location: "/admin", "Cache-Control": "no-store",
        "Set-Cookie": setCookie("kv_sid", sid, SESSION_DAYS * 86400, secure) } });
    }
    return new Response("Требуется авторизация", {
      status: 401,
      headers: {
        // realm строго ASCII: заголовок — ByteString, кириллица в нём роняет Response
        "WWW-Authenticate": 'Basic realm="KVANT Sourcing Dashboard", charset="UTF-8"',
        "Cache-Control": "no-store",
      },
    });
  }

  if (path === "/access/enter") {
    const p = await verify(env, url.searchParams.get("t"));
    if (!p || p.k !== "mag") return page("Ссылка не подошла",
      `<h1>Ссылка недействительна</h1><div class="card">Она истекла или уже была использована. ` +
      `<a href="/access/">Запросите вход заново</a>.</div>`, { contour: test });
    const used = await kv(env).get("n:" + p.jti);
    if (used) return page("Ссылка уже использована",
      `<h1>Ссылка уже использована</h1><div class="card">Одноразовая ссылка входа сработала раньше. ` +
      `<a href="/access/">Запросите новую</a>.</div>`, { contour: test });
    await kv(env).put("n:" + p.jti, "1", { expirationTtl: 60 * 60 * 48 });
    const u = await getUser(env, p.email);
    if (!u || !userTabs(u).length) return page("Доступ закрыт",
      `<h1>Доступ закрыт</h1><div class="card">Доступ отозван или истёк. <a href="/access/">Подать заявку</a>.</div>`, { contour: test });
    u.lastLogin = new Date().toISOString();
    await putUser(env, u);
    const sid = await sign(env, { k: "sid", email: u.email, exp: now() + SESSION_DAYS * 864e5 });
    return new Response(null, {
      status: 302,
      headers: { Location: "/", "Set-Cookie": setCookie("kv_sid", sid, SESSION_DAYS * 86400, secure), "Cache-Control": "no-store" },
    });
  }

  if (path === "/access/logout") {
    return new Response(null, {
      status: 302,
      headers: { Location: "/access/", "Set-Cookie": setCookie("kv_sid", "", 0, secure), "Cache-Control": "no-store" },
    });
  }

  // ---------- подтверждение владельцем: доступ по ссылке из письма ----------
  if (path === "/access/review") {
    const p = await verify(env, url.searchParams.get("t"));
    if (!p || p.k !== "rev") return page("Ссылка не подошла",
      `<h1>Ссылка недействительна</h1><div class="card">Срок подтверждения истёк (${REVIEW_DAYS} дней). ` +
      `Заявку видно в <a href="/admin">админке</a>.</div>`, { contour: test });
    const raw = await kv(env).get("r:" + p.id);
    if (!raw) return page("Заявка не найдена", `<h1>Заявка не найдена</h1>`, { contour: test });
    const req = JSON.parse(raw);

    if (request.method === "POST") {
      const f = await request.formData();
      const action = String(f.get("action") || "");
      const tabs = f.getAll("tabs").map(String).filter((t) => TAB_IDS.includes(t));
      const days = parseInt(String(f.get("days") || "0"), 10);
      req.decidedAt = new Date().toISOString();
      if (action === "deny" || !tabs.length) {
        req.status = "denied";
        await kv(env).put("r:" + req.id, JSON.stringify(req));
        await sendMail(env, { to: req.email, subject: "Доступ к дашборду КВАНТ не выдан",
          html: `<p>Заявка отклонена. Если это ошибка — напишите владельцу напрямую.</p>` });
        return page("Отказано", `<h1>Отказано</h1><div class="card">Заявителю отправлено уведомление.</div>`, { contour: test });
      }
      req.status = "approved";
      req.grantedTabs = tabs;
      await kv(env).put("r:" + req.id, JSON.stringify(req));
      const prev = await getUser(env, req.email);
      const u = await putUser(env, {
        ...(prev || {}), email: req.email, name: req.name || (prev && prev.name) || "",
        org: req.org || (prev && prev.org) || "", tabs, revoked: false,
        exp: days > 0 ? now() + days * 864e5 : null,
        createdAt: (prev && prev.createdAt) || new Date().toISOString(),
        grantedAt: new Date().toISOString(), grantedFrom: req.id,
      });
      await mailMagic(env, base, u, true);
      return page("Доступ выдан", `<h1>Доступ выдан</h1><div class="card"><b>${esc(u.email)}</b> — ` +
        tabs.map((t) => `<span class="pill on">${esc(TITLE[t])}</span>`).join("") +
        `<div class="note">Срок: ${u.exp ? new Date(u.exp).toLocaleDateString("ru-RU") : "бессрочно"}. ` +
        `Ссылка входа отправлена заявителю.</div></div>` +
        `<div class="row"><a class="btn" href="/admin">В админку</a></div>`, { contour: test });
    }
    if (req.status !== "pending") return page("Уже решено",
      `<h1>Заявка уже ${req.status === "approved" ? "одобрена" : "отклонена"}</h1>` +
      `<div class="card">Изменить решение можно в <a href="/admin">админке</a>.</div>`, { contour: test });
    return page("Подтверждение доступа", pageReviewBody(req, url.searchParams.get("t")), { contour: test });
  }

  // ---------- ящик писем тестового контура ----------
  if (path === "/access/outbox") {
    const open = test && (String(env.TEST_OUTBOX_OPEN || "") === "1" || (me && me.admin));
    if (!open) return new Response("Недоступно", { status: 404 });
    const mails = (await listAll(env, "m:")).sort((a, b) => (a.at < b.at ? 1 : -1));
    return page("Ящик контура", `<h1>Ящик тестового контура</h1>` +
      `<div class="note">Здесь оседают письма, пока не подключён почтовый провайдер. Ссылки кликабельны.</div>` +
      (mails.length ? mails.map((m) => `<div class="card"><div class="note mono">${esc(m.at)} → ${esc(m.to)}</div>` +
        `<div style="margin:6px 0 8px"><b>${esc(m.subject)}</b></div>` +
        linksOf(m.html).map((l) => `<div><a href="${esc(l)}">${esc(l)}</a></div>`).join("") +
        `<div class="note">${esc(stripTags(m.html)).slice(0, 400)}</div></div>`).join("")
        : `<div class="card">Писем пока нет.</div>`), { contour: test });
  }

  // ---------- админка владельца ----------
  if (path === "/admin" || path.startsWith("/admin/")) {
    if (!me || !me.admin) return needAuth(env, url, test, me);
    if (path === "/admin/user" && request.method === "POST") {
      const f = await request.formData();
      const email = normEmail(f.get("email"));
      const u = (await getUser(env, email)) || { email, createdAt: new Date().toISOString() };
      const act = String(f.get("action") || "save");
      if (act === "revoke") { u.revoked = true; u.tabs = []; }
      else if (act === "restore") { u.revoked = false; }
      else {
        u.tabs = f.getAll("tabs").map(String).filter((t) => TAB_IDS.includes(t));
        const days = parseInt(String(f.get("days") || "0"), 10);
        if (days !== -1) u.exp = days > 0 ? now() + days * 864e5 : null;   // -1 — «не трогать срок»
        u.revoked = false;
        u.admin = f.get("admin") === "1";
      }
      await putUser(env, u);
      return new Response(null, { status: 302, headers: { Location: "/admin", "Cache-Control": "no-store" } });
    }
    if (path === "/admin/invite" && request.method === "POST") {
      const f = await request.formData();
      const email = normEmail(f.get("email"));
      const u = await getUser(env, email);
      if (u && userTabs(u).length) await mailMagic(env, base, u);
      return new Response(null, { status: 302, headers: { Location: "/admin", "Cache-Control": "no-store" } });
    }
    const users = (await listAll(env, "u:")).sort((a, b) => String(a.email).localeCompare(String(b.email)));
    const reqs = (await listAll(env, "r:")).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
    return page("Админка доступов", await pageAdminBody(env, users, reqs, base, test), { contour: test });
  }

  // ---------- дальше — сам дашборд, только для аутентифицированных ----------
  if (!me) return needAuth(env, url, test, null);

  // /gen — лёгкая проверка свежести (поллинг со страницы)
  if (path === "/gen") {
    const idx = await env.ASSETS.fetch(new Request(url.origin + "/"));
    const text = await idx.text();
    const m = text.match(/gen=new Date\("([^"]+)"\)/);
    return new Response(JSON.stringify({ gen: m ? m[1] : null }), {
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }
  if (path.startsWith("/fonts/")) {
    const font = await env.ASSETS.fetch(request);
    const fh = new Headers(font.headers);
    fh.set("Cache-Control", "public, max-age=31536000, immutable");
    return new Response(font.body, { status: font.status, headers: fh });
  }

  const resp = await env.ASSETS.fetch(request);
  const headers = new Headers(resp.headers);
  headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
  const ctype = resp.headers.get("content-type") || "";
  if (!ctype.includes("text/html")) return new Response(resp.body, { status: resp.status, headers });
  const html = applyAcl(await resp.text(), me.tabs, me);
  headers.delete("Content-Encoding");
  headers.delete("Content-Length");
  headers.set("Content-Type", "text/html; charset=utf-8");
  return new Response(html, { status: resp.status, headers });
}

// ---------------------------------------------------------------------------
// СТРАНИЦЫ
// ---------------------------------------------------------------------------
function needAuth(env, url, test, me) {
  const body = `<div class="eyebrow">Квант · дашборд сорсинга</div>` +
    (me ? `<h1>Недостаточно прав</h1><div class="card">Раздел только для владельца. Вы вошли как <b>${esc(me.email)}</b>. ` +
      `<a href="/">Вернуться к дашборду</a>.</div>`
      : `<h1>Нужен доступ</h1><div class="card">Дашборд закрыт. Войдите по ссылке из почты или запросите доступ ` +
        `— владелец подтвердит его письмом.</div><div class="row"><a class="btn" href="/access/">Запросить доступ / войти</a>` +
        `<a class="btn gh" href="/access/basic">Вход владельца по паролю</a></div>`);
  return page("Доступ", body, { contour: test });
}

function pageRequest(env, url, test, { err } = {}) {
  return page("Запрос доступа",
    `<div class="eyebrow">Квант · дашборд сорсинга</div><h1>Запрос доступа</h1>` +
    `<div class="note">Отметьте вкладки, которые нужны для работы. Заявка уйдёт владельцу — он подтвердит доступ письмом ` +
    `и может сузить набор вкладок.</div>` +
    (err ? `<div class="card bad">${esc(err)}</div>` : "") +
    `<form method="POST" action="/access/request" class="card">` +
    `<label>Рабочая почта *</label><input type="email" name="email" required placeholder="ivanov@kvant.ru">` +
    `<label>Имя и фамилия</label><input type="text" name="name" placeholder="Иван Иванов">` +
    `<label>Подразделение / должность</label><input type="text" name="org" placeholder="Отдел сорсинга, руководитель">` +
    `<label>Какие вкладки нужны *</label>${tabChecks([])}` +
    `<label>Зачем нужен доступ</label><textarea name="reason" placeholder="Смотреть просрочки по своим сделкам"></textarea>` +
    `<div class="row"><button type="submit">Отправить заявку</button></div></form>` +
    `<h2>Уже есть доступ</h2>` +
    `<form method="POST" action="/access/login" class="card">` +
    `<label>Почта</label><input type="email" name="email" required placeholder="ivanov@kvant.ru">` +
    `<div class="row"><button class="gh" type="submit">Прислать ссылку входа</button></div>` +
    `<div class="note">Пароля нет: вход по одноразовой ссылке, дальше сессия держится 30 дней.</div></form>`,
    { contour: test });
}

function pageReviewBody(req, token) {
  return `<div class="eyebrow">Квант · подтверждение доступа</div><h1>Заявка на доступ</h1>` +
    `<div class="card"><table>` +
    `<tr><th>Почта</th><td class="mono">${esc(req.email)}</td></tr>` +
    `<tr><th>Имя</th><td>${esc(req.name) || "—"}</td></tr>` +
    `<tr><th>Подразделение</th><td>${esc(req.org) || "—"}</td></tr>` +
    `<tr><th>Обоснование</th><td>${esc(req.reason) || "—"}</td></tr>` +
    `<tr><th>Подана</th><td>${esc(req.createdAt)}</td></tr>` +
    `<tr><th>Просит вкладки</th><td>${req.tabs.map((t) => `<span class="pill on">${esc(TITLE[t])}</span>`).join("")}</td></tr>` +
    `</table></div>` +
    `<form method="POST" action="/access/review?t=${esc(token)}" class="card">` +
    `<label>Что выдать (галочки уже стоят по заявке — снимите лишнее)</label>${tabChecks(req.tabs)}` +
    `<label>Срок доступа</label><select name="days">` +
    `<option value="90">90 дней</option><option value="180">180 дней</option>` +
    `<option value="365">1 год</option><option value="0">бессрочно</option></select>` +
    `<div class="row"><button type="submit" name="action" value="approve">Выдать доступ</button>` +
    `<button type="submit" name="action" value="deny" class="dg">Отказать</button></div>` +
    `<div class="note">После выдачи заявителю уйдёт письмо с одноразовой ссылкой входа. ` +
    `Отозвать доступ в любой момент можно в админке.</div></form>`;
}

async function pageAdminBody(env, users, reqs, base, test) {
  const pending = reqs.filter((r) => r.status === "pending");
  let h = `<div class="eyebrow">Квант · дашборд сорсинга</div><h1>Доступы</h1>` +
    `<div class="row"><a class="btn" href="/">К дашборду</a>` +
    (test ? `<a class="btn gh" href="/access/outbox">Ящик контура</a>` : "") + `</div>`;

  h += `<h2>Заявки на подтверждении (${pending.length})</h2>`;
  if (!pending.length) h += `<div class="card">Новых заявок нет.</div>`;
  for (const r of pending) {
    const t = await sign(env, { k: "rev", id: r.id, exp: now() + REVIEW_DAYS * 864e5 });
    h += `<div class="card"><div><b>${esc(r.name || r.email)}</b> <span class="mono note">${esc(r.email)}</span></div>` +
      `<div class="note">${esc(r.org) || "—"} · ${esc(r.createdAt)}</div>` +
      `<div style="margin:8px 0">${r.tabs.map((x) => `<span class="pill on">${esc(TITLE[x])}</span>`).join("")}</div>` +
      (r.reason ? `<div class="note">${esc(r.reason)}</div>` : "") +
      `<div class="row"><a class="btn" href="${esc(base)}/access/review?t=${esc(t)}">Рассмотреть</a></div></div>`;
  }

  h += `<h2>Пользователи (${users.length})</h2>`;
  if (!users.length) h += `<div class="card">Пока никого. Доступ появляется после подтверждения заявки.</div>`;
  for (const u of users) {
    const live = userTabs(u);
    const state = u.revoked ? `<span class="bad">отозван</span>`
      : u.exp && u.exp < now() ? `<span class="bad">истёк</span>`
      : `<span class="ok">активен</span>`;
    h += `<form method="POST" action="/admin/user" class="card">` +
      `<input type="hidden" name="email" value="${esc(u.email)}">` +
      `<div><b class="mono">${esc(u.email)}</b> — ${state}${u.admin ? ` <span class="pill">админ</span>` : ""}</div>` +
      `<div class="note">${esc(u.name) || "—"}${u.org ? " · " + esc(u.org) : ""} · доступ до ` +
      `${u.exp ? new Date(u.exp).toLocaleDateString("ru-RU") : "бессрочно"}` +
      `${u.lastLogin ? " · был " + esc(String(u.lastLogin).slice(0, 16).replace("T", " ")) : " · ни разу не входил"}</div>` +
      tabChecks(live) +
      `<label>Срок</label><select name="days">` +
      (u.exp ? `<option value="-1" selected>не менять (до ${new Date(u.exp).toLocaleDateString("ru-RU")})</option>` : "") +
      `<option value="0"${!u.exp ? " selected" : ""}>бессрочно</option>` +
      `<option value="90">90 дней</option><option value="180">180 дней</option><option value="365">1 год</option></select>` +
      `<label style="display:flex;gap:8px;align-items:center;margin-top:12px">` +
      `<input type="checkbox" name="admin" value="1"${u.admin ? " checked" : ""}> админ (все вкладки + эта страница)</label>` +
      `<div class="row"><button type="submit" name="action" value="save">Сохранить</button>` +
      (u.revoked ? `<button type="submit" name="action" value="restore" class="gh">Вернуть</button>`
        : `<button type="submit" name="action" value="revoke" class="dg">Отозвать доступ</button>`) +
      `<button type="submit" formaction="/admin/invite" class="gh">Выслать ссылку входа</button></div></form>`;
  }

  const decided = reqs.filter((r) => r.status !== "pending").slice(0, 25);
  if (decided.length) {
    h += `<h2>История заявок</h2><div class="card"><table><tr><th>Дата</th><th>Кто</th><th>Решение</th><th>Вкладки</th></tr>` +
      decided.map((r) => `<tr><td class="mono">${esc(String(r.createdAt).slice(0, 16).replace("T", " "))}</td>` +
        `<td>${esc(r.email)}</td><td>${r.status === "approved" ? '<span class="ok">выдан</span>' : '<span class="bad">отказ</span>'}</td>` +
        `<td>${(r.grantedTabs || r.tabs).map((x) => esc(TITLE[x])).join(", ")}</td></tr>`).join("") +
      `</table></div>`;
  }
  return h;
}

// ---------------------------------------------------------------------------
// ПИСЬМА
// ---------------------------------------------------------------------------
function mailOwner(req, link) {
  return `<p><b>Запрос доступа к дашборду КВАНТ</b></p>` +
    `<p>Кто: ${esc(req.name || "—")} &lt;${esc(req.email)}&gt;<br>` +
    `Подразделение: ${esc(req.org || "—")}<br>` +
    `Обоснование: ${esc(req.reason || "—")}</p>` +
    `<p>Просит вкладки: <b>${req.tabs.map((t) => esc(TITLE[t])).join(", ")}</b></p>` +
    `<p>Подтвердить или сузить набор вкладок: <a href="${link}">${link}</a></p>` +
    `<p style="color:#888;font-size:12px">Ссылка действует ${REVIEW_DAYS} дней. Никому её не пересылайте: ` +
    `открывший её может выдать доступ от вашего имени.</p>`;
}
async function mailMagic(env, base, u, granted = false) {
  const t = await sign(env, { k: "mag", email: u.email, jti: uid(), exp: now() + MAGIC_MIN * 60000 });
  const link = `${base}/access/enter?t=${t}`;
  const tabs = userTabs(u).map((x) => TITLE[x]).join(", ");
  await sendMail(env, {
    to: u.email,
    subject: granted ? "Доступ к дашборду КВАНТ выдан" : "Ссылка входа в дашборд КВАНТ",
    html: (granted ? `<p>Владелец подтвердил доступ.</p>` : "") +
      `<p>Ваши вкладки: <b>${esc(tabs)}</b></p>` +
      `<p>Вход: <a href="${link}">${link}</a></p>` +
      `<p style="color:#888;font-size:12px">Ссылка одноразовая и действует сутки. Дальше вход держится ${SESSION_DAYS} дней.</p>`,
  });
  return link;
}
