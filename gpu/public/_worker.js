// Гейт сайта «Библиотека ГПУ»: вход только через Cloudflare Access (портал КВАНТ).
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы через этот
// fetch; файлы отдаём через env.ASSETS уже после проверки подписи входа.
// Внутри стоимость лота с вилками закупки, разрывы цен OEM/аналог и рейтинг поставщиков.
// Паролей нет: периметр — приложение Access с одной политикой допуска по почте
// (распоряжение владельца от 07.09.2026: единый вход, единый портал).

export default {
  async fetch(request, env) {
    const who = await accessOk(request, env);
    if (!who) return denyPage("Библиотека ГПУ · КВАНТ");
    if (!(await siteAllowed(request, env, SITE))) return denyPage("Библиотека ГПУ · КВАНТ");

    const resp = await env.ASSETS.fetch(request);
    const out = new Response(resp.body, resp);
    // страницу не кэшируем: данные обновляются пересборкой
    out.headers.set("Cache-Control", "no-store");
    out.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
    out.headers.set("Referrer-Policy", "no-referrer");
    return out;
  },
};

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

const SITE = "gpu";
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
