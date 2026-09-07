// Портал КВАНТ и дашборд сорсинга: вход только через Cloudflare Access + САМООБНОВЛЕНИЕ.
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы через этот
// fetch; файлы отдаём через env.ASSETS уже после проверки подписи входа.
//
// Маршруты:  /            — портал: плитки всех сайтов компании (генерируется здесь)
//            /dashboard   — дашборд сорсинга (index.html из сборки) с плашкой портала
//            /gen         — метка свежести для поллинга со страницы
//            /fonts/*     — иммутабельная статика
// Паролей нет: периметр — приложение Access с одной политикой допуска по почте
// (распоряжение владельца от 07.09.2026: единый вход, единый портал).
//
// САМООБНОВЛЕНИЕ: если при заходе данные старше 2 ч, воркер сам триггерит пересборку
// (GitHub repository_dispatch {"event_type":"rebuild"}), а страница опрашивает сервер
// и перезагрузится, когда придёт свежий деплой. Нужен секрет проекта GH_DISPATCH_TOKEN —
// fine-grained PAT этого репозитория с правом Contents: Read and write. Без него
// самообновление выключено, ломаться нечему.
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const FRESH_MS = 2 * 3600 * 1000;          // порог свежести — 2 часа
const DEBOUNCE_MS = 15 * 60;               // не триггерить пересборку чаще раза в 15 мин (сек, для Cache-Control)
const PORTAL_SITES = [
  { href: "/dashboard", name: "Дашборд сорсинга", note: "нагрузка, конверсия, реализация, поставщики — по данным Bitrix24, обновление каждые 2 часа" },
  { href: "https://kvant-zip.pages.dev/", name: "База ЗИП", note: "позиции, ODM-аналоги и цены; внутри — справочник ГТУ и документы заказов" },
  { href: "https://kvant-zip.pages.dev/gt/", name: "Справочник ГТУ", note: "газотурбинное оборудование: поставщики, цепочки, досье" },
  { href: "https://kvant-gpu.pages.dev/", name: "Библиотека ГПУ", note: "газопоршневые установки: Cummins, Caterpillar, INNIO — сорсинг и цены" },
  { href: "https://kvant-ove.pages.dev/", name: "ОВЭ-75", note: "проект «обжиг – выщелачивание – электроэкстракция» для Кольской ГМК" },
  { href: "https://kvant-gidromet.pages.dev/", name: "Гидрометаллургия", note: "заключение по переработке медно-золотого концентрата" },
  { href: "https://kvant-gok.pages.dev/", name: "ГОК", note: "базовый проект золото-медного ГОКа" },
];

export default {
  async fetch(request, env, ctx) {
    const who = await accessOk(request, env);
    if (!who) return denyPage("Портал КВАНТ");

    const url = new URL(request.url);

    // портал — корень
    if (url.pathname === "/" || url.pathname === "/portal" || url.pathname === "/portal/") {
      if (env.VISITS && request.headers.get("X-Poll") !== "1") ctx.waitUntil(logVisit(request, env));
      return portalPage(who, env);
    }

    // /gen — лёгкая проверка свежести для поллинга со страницы дашборда.
    // Заодно триггерит пересборку, если данные устарели (как заход на страницу).
    if (url.pathname === "/gen") {
      const idx = await env.ASSETS.fetch(new Request(url.origin + "/index.html", { headers: request.headers }));
      const text = await idx.text();
      const m = text.match(/gen=new Date\("([^"]+)"\)/);
      const genMs = m ? Date.parse(m[1]) : NaN;
      const stale = isNaN(genMs) || (Date.now() - genMs) > FRESH_MS;
      if (stale && env.GH_DISPATCH_TOKEN) ctx.waitUntil(triggerRebuild(env));
      return new Response(JSON.stringify({ gen: m ? m[1] : null }), {
        headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
      });
    }

    // шрифты — иммутабельная статика: кэшируем надолго (имена файлов стабильны)
    if (url.pathname.startsWith("/fonts/")) {
      const font = await env.ASSETS.fetch(request);
      const fh = new Headers(font.headers);
      fh.set("Cache-Control", "public, max-age=31536000, immutable");
      return new Response(font.body, { status: font.status, statusText: font.statusText, headers: fh });
    }

    // дашборд: /dashboard (и прямой /index.html) → index.html из сборки
    const isDash = url.pathname === "/dashboard" || url.pathname === "/dashboard/" || url.pathname === "/index.html";
    const resp = await env.ASSETS.fetch(isDash ? new Request(url.origin + "/index.html", { headers: request.headers }) : request);
    const headers = new Headers(resp.headers);
    // ЗАПРЕЩАЕМ кэширование: иначе браузер/edge отдают старый index.html и «сайт не обновляется»
    headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    headers.set("Pragma", "no-cache");
    headers.set("Expires", "0");
    headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");

    const ctype = resp.headers.get("content-type") || "";
    if (ctype.includes("text/html")) {
      // лог визита для ежедневного отчёта (уникальные IP). Поллинг свежести (X-Poll) не считаем.
      if (env.VISITS && request.headers.get("X-Poll") !== "1") ctx.waitUntil(logVisit(request, env));
      try {
        const buf = await resp.arrayBuffer();
        let text = new TextDecoder().decode(buf);
        const m = text.match(/gen=new Date\("([^"]+)"\)/);
        const genMs = m ? Date.parse(m[1]) : NaN;
        const stale = isNaN(genMs) || (Date.now() - genMs) > FRESH_MS;
        if (stale && env.GH_DISPATCH_TOKEN) ctx.waitUntil(triggerRebuild(env));
        // плашка портала над вкладками
        text = text.replace('<div class="tabs">', portalBar(who, env) + '<div class="tabs">');
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

// Портал: одна страница с плитками всех сайтов компании. Шрифты — свои (/fonts), внешних ресурсов нет.
function portalPage(who, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tiles = PORTAL_SITES.map((s) =>
    `<a class="tile" href="${esc(s.href)}"><div class="n">${esc(s.name)}</div><div class="d">${esc(s.note)}</div></a>`).join("");
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
.note{color:var(--dim);font-size:12.5px;margin-top:22px;max-width:70ch}
</style></head><body><div class="wrap">
<div class="top"><div><div class="eyebrow">КВАНТ · единый вход</div><h1>Портал</h1></div>
<div class="who">${esc(who.email)}<a href="https://${esc(team)}/cdn-cgi/access/logout">выйти</a></div></div>
<div class="grid">${tiles}</div>
<div class="note">Вход по корпоративной почте через Cloudflare, сессия действует месяц. Доступ к сайтам выдаёт владелец.</div>
</div></body></html>`;
  return new Response(html, { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow, noarchive", "Referrer-Policy": "no-referrer" } });
}
// плашка над дашбордом: ссылка на портал, почта вошедшего, выход
function portalBar(who, env) {
  const team = String((env && env.CF_ACCESS_TEAM) || CF_TEAM_DEFAULT).replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  return `<div style="display:flex;gap:14px;align-items:center;justify-content:space-between;font:12px/1.4 'IBM Plex Sans',sans-serif;color:#8b97a8;padding:8px 14px 0">` +
    `<a href="/" style="color:#5aa9ff;text-decoration:none">← Портал КВАНТ</a>` +
    `<span>${esc(who.email)} · <a href="https://${esc(team)}/cdn-cgi/access/logout" style="color:#5aa9ff;text-decoration:none">выйти</a></span></div>`;
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
