// Гейт сайта «ГШО — горно-шахтное оборудование»: вход только через Cloudflare Access
// (портал КВАНТ). Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ
// запросы через этот fetch; файлы отдаём через env.ASSETS уже после проверки подписи входа.
// Внутри база ГШО (перфораторы, буровая техника, ЗИП), библиотека ГТУ (/gt/) и документы
// заказов (/orders/). Файл копируется сборщиком zip/build.py в zip/public/.
// На портале это ДВЕ плитки с разными правами, поэтому /gt/ спрашивает право «gt»,
// а всё остальное — право «zip» (идентификатор оставлен прежним, чтобы переименование
// не отняло доступ у тех, кому он уже выдан).
// База ЗИП: страница больше НЕ носит ключ Supabase. Запросы идут на /db/* этого же
// сайта, и ключ подставляет воркер — уже после проверки подписи Access и права «zip».
// Пока в Cloudflare не задан секрет SUPABASE_SERVICE_KEY, используется прежний
// публикуемый ключ (см. SUPA_FALLBACK_KEY): сайт работает как раньше, но ключ из
// браузера уже исчез. Полное закрытие — секрет + zip/supabase/migrations_rls.sql.
// Паролей нет: периметр — приложение Access с одной политикой допуска по почте
// (распоряжение владельца от 07.09.2026: единый вход, единый портал).

export default {
  async fetch(request, env) {
    const who = await accessOk(request, env);
    if (!who) return denyPage("ГШО · КВАНТ");

    const path = new URL(request.url).pathname;
    const gt = path === "/gt" || path.startsWith("/gt/");   // без слэша Pages сам перебросит
    // Служебные адреса страниц (кто вошёл, доступ к базе) общие для обоих разделов:
    // они лежат не под /gt/, и проверять их правом «zip» нельзя — инженер библиотеки
    // ГТУ, у которого права «zip» нет, иначе не смог бы ни подписать заметку, ни
    // сохранить её. Поэтому здесь достаточно ЛЮБОГО из двух прав.
    const api = path === "/api/me" || path === "/db" || path.startsWith("/db/");
    const allowed = api
      ? (await siteAllowed(request, env, SITE)) || (await siteAllowed(request, env, "gt"))
      : await siteAllowed(request, env, gt ? "gt" : SITE);
    if (!allowed) return denyPage(gt ? "Библиотека ГТУ · КВАНТ" : "ГШО · КВАНТ");

    // Кто вошёл — для подписи правок в библиотеке ГТУ. Отдаём только собственную
    // почту спрашивающего: она и так уже у него, узнать чужую этим нельзя.
    if (path === "/api/me") {
      return new Response(JSON.stringify({ email: who.email }), {
        headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
      });
    }

    if (path === "/db" || path.startsWith("/db/")) return proxyDb(request, env, who);

    const resp = await env.ASSETS.fetch(request);
    const out = new Response(resp.body, resp);
    // страницу не кэшируем: данные обновляются пересборкой
    out.headers.set("Cache-Control", "no-store");
    out.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
    out.headers.set("Referrer-Policy", "no-referrer");
    return out;
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Прокси к Supabase. Вызывается ТОЛЬКО после accessOk() и siteAllowed(): без
// подписи входа сюда не попасть. Браузер шлёт заглушку вместо ключа — воркер её
// перезаписывает, поэтому настоящий ключ живёт только на стороне Cloudflare.
const SUPA_ORIGIN = "https://vpjliavuuxjcvtxbthlp.supabase.co";
// Прежний публикуемый ключ. Это не новая утечка: он и так лежал в отдаваемой
// странице и остаётся в истории git. Здесь он — страховка на время перехода,
// чтобы выкладка прокси ничего не сломала до того, как владелец заведёт секрет.
// После задания SUPABASE_SERVICE_KEY и сужения RLS этот ключ станет бесполезен.
const SUPA_FALLBACK_KEY = "sb_publishable_z74BF5VzezeQfTc9fni-ZA_HMyTKRTJ";

// Правки инженеров в библиотеке ГТУ: таблица gt_notes. Автора проставляет воркер по
// подписи Cloudflare Access, а не страница: подпись, которую можно подделать из
// браузера, не подпись. Удаление запрещено — снятая правка помечается removed и
// остаётся в истории, иначе «пропало» повторится, только уже необратимо.
const NOTES_PATH = "/rest/v1/gt_notes";

// The browser only needs these ZIP tables and GT notes. A service key must never
// turn /db into a general PostgREST, Storage, Auth, or Functions gateway.
const ZIP_DB_METHODS = new Map([
  ["positions", ["GET", "HEAD", "PATCH"]],
  ["odm_suppliers", ["GET", "HEAD", "PATCH"]],
  ["price_records", ["GET", "HEAD", "POST", "PATCH", "DELETE"]],
  ["drawings", ["GET", "HEAD", "POST", "PATCH", "DELETE"]],
  ["samples", ["GET", "HEAD", "POST", "PATCH", "DELETE"]],
  ["rfq_requests", ["POST"]],
  ["change_log", ["GET", "HEAD"]],
  ["gt_notes", ["GET", "HEAD", "POST", "PATCH"]],
]);
const ZIP_FILE_BUCKETS = new Set(["drawings", "samples"]);

function dbReject(error, status = 403) {
  return new Response(JSON.stringify({ error }), { status, headers: {
    "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
  } });
}

function zipObjectKey(value) {
  return typeof value === "string" && value.length > 0 && value.length <= 2048 &&
    !/[\\%\x00-\x1f\x7f]/.test(value) &&
    value.split("/").every((part) => part && part !== "." && part !== "..");
}

function zipRestQuery(url, table) {
  const seen = new Set();
  for (const [name, value] of url.searchParams) {
    if (seen.has(name) || /[\x00-\x1f\x7f]/.test(value) || value.length > 4096) return false;
    seen.add(name);
    if (name === "select") {
      // No embedded relations, computed projections, aliases, casts, or schema.
      if (value !== "*") return false;
    } else if (name === "order") {
      // Ordering by an arbitrary computed field can execute a database function
      // even when select is '*'. The two clients only sort the physical 'at'.
      if (!["change_log", "gt_notes"].includes(table) || !["at.asc", "at.desc"].includes(value)) return false;
    } else if (name === "limit" || name === "offset") {
      if (!/^[0-9]{1,9}$/.test(value)) return false;
    } else if (name === "id") {
      if (!["positions", "odm_suppliers", "price_records", "drawings", "samples"].includes(table) ||
          !/^eq\.[0-9]+$/.test(value)) return false;
    } else if (name === "scope") {
      if (table !== "gt_notes" || !value.startsWith("eq.") || value.length < 4) return false;
    } else if (name === "removed") {
      if (table !== "gt_notes" || (value !== "is.false" && value !== "is.true")) return false;
    } else return false;
  }
  return true;
}

function zipDbRoute(url, method) {
  const rest = /^\/db\/rest\/v1\/([a-z_]+)$/.exec(url.pathname);
  if (rest) {
    const methods = ZIP_DB_METHODS.get(rest[1]);
    if (!methods) return { error: "db_route_forbidden" };
    if (!methods.includes(method)) return { error: "db_method_forbidden", status: 405 };
    if (!zipRestQuery(url, rest[1])) return { error: "db_query_forbidden" };
    return { kind: "rest", notes: rest[1] === "gt_notes" };
  }
  const storage = /^\/db\/storage\/v1\/object\/(sign\/)?(drawings|samples)(?:\/(.+))?$/.exec(url.pathname);
  if (!storage || !ZIP_FILE_BUCKETS.has(storage[2])) return { error: "db_route_forbidden" };
  let objectKey;
  try { objectKey = storage[3] === undefined ? null : decodeURIComponent(storage[3]); }
  catch { return { error: "db_route_forbidden" }; }
  if (objectKey !== null && !zipObjectKey(objectKey)) return { error: "db_route_forbidden" };
  const signed = !!storage[1];
  const kind = signed && objectKey && method === "POST" ? "sign" :
    signed && objectKey && (method === "GET" || method === "HEAD") ? "download" :
    !signed && objectKey && method === "POST" ? "upload" :
    !signed && objectKey === null && method === "DELETE" ? "remove" : null;
  if (!kind) return { error: "db_method_forbidden", status: 405 };
  const seen = new Set();
  for (const [name, value] of url.searchParams) {
    if (kind !== "download" || seen.has(name) || !["token", "download"].includes(name) ||
        value.length > 16384 || /[\x00-\x1f\x7f]/.test(value)) return { error: "db_query_forbidden" };
    seen.add(name);
  }
  if (kind === "download" && !url.searchParams.get("token")) return { error: "db_query_forbidden" };
  return { kind, notes: false };
}

async function zipStorageBody(request, kind) {
  // Only control requests are JSON. An upload remains file bytes, never a
  // generic Storage command that can choose a different source/destination.
  const length = request.headers.get("Content-Length");
  if (length && (!/^[0-9]+$/.test(length) || Number(length) > 65536)) return null;
  const reader = request.body?.getReader();
  if (!reader) return null;
  const chunks = []; let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 65536) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let data;
  try { data = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { return null; }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  if (kind === "sign") {
    if (Object.keys(data).length !== 1 || !Number.isSafeInteger(data.expiresIn) ||
        data.expiresIn < 1 || data.expiresIn > 3600) return null;
  } else if (Object.keys(data).length !== 1 || !Array.isArray(data.prefixes) ||
      !data.prefixes.length || data.prefixes.length > 100 || !data.prefixes.every(zipObjectKey)) return null;
  return JSON.stringify(data);
}

async function stampAuthor(request, who) {
  const body = await request.text();
  if (!body) return body;
  let data;
  try { data = JSON.parse(body); } catch { return body; }   // не JSON — не наше дело
  const stamp = (r) => {
    if (!r || typeof r !== "object") return r;
    r.author = who && who.email ? who.email : "";
    r.at = new Date().toISOString();
    return r;
  };
  return JSON.stringify(Array.isArray(data) ? data.map(stamp) : stamp(data));
}

async function proxyDb(request, env, who) {
  const url = new URL(request.url);
  // Keep the explicit notes guard and its existing user-facing explanation.
  if (url.pathname === "/db" + NOTES_PATH && request.method === "DELETE")
    return dbReject("правки не удаляются: снимайте флагом removed", 405);
  const route = zipDbRoute(url, request.method);
  if (route.error) return dbReject(route.error, route.status || 403);
  for (const name of ["Accept-Profile", "Content-Profile"]) {
    const value = request.headers.get(name);
    if (value !== null && value !== "public") return dbReject("db_schema_forbidden");
  }
  let storageBody;
  if (route.kind === "sign" || route.kind === "remove") {
    try { storageBody = await zipStorageBody(request, route.kind); }
    catch { return dbReject("db_body_invalid", 400); }
    if (storageBody === null) return dbReject("db_body_invalid", 400);
  }
  const target = SUPA_ORIGIN + url.pathname.slice("/db".length) + url.search;
  const key = (env && env.SUPABASE_SERVICE_KEY) || SUPA_FALLBACK_KEY;
  const notes = route.notes;
  const headers = new Headers();
  for (const name of ["Accept", "Content-Type", "Prefer", "Range", "Range-Unit", "X-Upsert", "Cache-Control"]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  headers.set("apikey", key);
  if (!key.startsWith("sb_secret_")) headers.set("Authorization", "Bearer " + key);
  if (route.kind === "rest") {
    headers.set("Accept-Profile", "public");
    headers.set("Content-Profile", "public");
  }

  const method = request.method;
  const init = { method, headers, redirect: "manual" };
  if (method !== "GET" && method !== "HEAD") {
    init.body = storageBody !== undefined ? storageBody : notes ? await stampAuthor(request, who) : request.body;
    if (notes || storageBody !== undefined) headers.set("Content-Type", "application/json");
  }

  const upstream = await fetch(target, init);
  const out = new Response(upstream.body, upstream);
  out.headers.set("Cache-Control", "no-store");
  out.headers.delete("set-cookie");
  return out;
}

// экспорт для тестов (на исполнение воркера не влияет)
export { proxyDb, SUPA_ORIGIN, stampAuthor, NOTES_PATH };

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

const SITE = "zip";
// BEGIN siteRights
const RIGHTS_URL = "https://kvant-sourcing-f122.pages.dev/api/rights";
const RIGHTS_TTL = 60 * 1000;              // память изолята: не дёргать портал на каждый файл
const rightsCache = new Map();

// Что стоит журнала: страницы и выгружаемые файлы. Разметка, картинки, шрифты и
// обращения страницы к данным (/db/…) — часть страницы, а не действие человека,
// и в журнал не идут.
const AUDIT_SKIP = /^\/db\/|^\/api\/|\.(css|js|mjs|map|woff2?|ttf|png|jpe?g|gif|svg|webp|ico|avif)$/i;

async function siteAllowed(request, env, site) {
  if (env && env.SITE_RIGHTS === "off") return true;
  const jwt = request.headers.get("Cf-Access-Jwt-Assertion") || readCookie(request, "CF_Authorization");
  if (!jwt) return true;                   // сюда приходят только вошедшие; подпись проверена выше
  const path = new URL(request.url).pathname;
  const worth = !AUDIT_SKIP.test(path);
  const now = Date.now();
  const hit = rightsCache.get(jwt);
  // Права берём из памяти изолята, но о самом действии портал должен узнать: журналу
  // нужны все обращения, а не первое в минуту. Запись идёт отдельным запросом и
  // ответа не ждёт — задержать отдачу страницы она не может.
  if (hit && now - hit.t < RIGHTS_TTL) {
    if (worth) tell(jwt, site, path);
    return hit.sites === null || hit.sites.includes(site);
  }
  let sites = null;                        // null — «портал не ответил», пускаем
  try {
    const r = await fetch(rightsUrl(site, worth ? path : ""), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    if (r.ok) {
      const d = await r.json();
      if (d && Array.isArray(d.sites)) sites = d.sites;
    }
  } catch { /* сеть между проектами не должна запирать сайт */ }
  if (rightsCache.size > 500) rightsCache.clear();
  rightsCache.set(jwt, { t: now, sites });
  return sites === null || sites.includes(site);
}

function rightsUrl(site, path) {
  const u = new URL(RIGHTS_URL);
  if (path) { u.searchParams.set("site", site); u.searchParams.set("at", path); }
  return u.toString();
}

function tell(jwt, site, path) {
  try {
    const p = fetch(rightsUrl(site, path), { headers: { "Cf-Access-Jwt-Assertion": jwt } });
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch { /* журнал не должен мешать работе сайта */ }
}
// END siteRights

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
