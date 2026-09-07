// Гейт сайта ОВЭ-75: вход только через Cloudflare Access (портал КВАНТ).
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы через этот
// fetch; файлы отдаём через env.ASSETS уже после проверки подписи входа.
// Внутри тендерные данные КГМК/Гипроникеля, пул поставщиков и досье на конкурента.
// Паролей нет: периметр — приложение Access с одной политикой допуска по почте
// (распоряжение владельца от 07.09.2026: единый вход, единый портал).
//
// POST /api/answers — приём ответов с вкладки «Вопросы Заказчику»: коммит в
// ove/data/questions_answers.json ветки main через GitHub Contents API (секрет проекта
// GH_ANSWERS_TOKEN — fine-grained PAT с правом Contents: Read and write). Только для вошедших.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/answers") return handleAnswers(request, env);

    const who = await accessOk(request, env);
    if (!who) return denyPage("ОВЭ-75 · КВАНТ");

    const resp = await env.ASSETS.fetch(request);
    const out = new Response(resp.body, resp);
    out.headers.set("Cache-Control", "no-store");
    out.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
    out.headers.set("Referrer-Policy", "no-referrer");
    return out;
  },
};

// ── /api/answers ─────────────────────────────────────────────────────────────
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const GH_PATH = "ove/data/questions_answers.json";
const GH_BRANCH = "main";
const ST_ALLOWED = new Set(["sent", "answered", "closed"]);

async function handleAnswers(request, env) {
  const json = (o, status = 200, extra = {}) => new Response(JSON.stringify(o), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
  if (request.method !== "POST") return json({ ok: false, error: "post_only" }, 405);

  // отправка только вошедшим через Cloudflare Access: анонимные коммиты недопустимы
  if (!(await accessOk(request, env))) return json({ ok: false, error: "auth" }, 403);
  if (!env.GH_ANSWERS_TOKEN) return json({ ok: false, error: "no_token" }, 503);

  let body;
  try { body = await request.json(); } catch { return json({ ok: false, error: "bad_json" }, 400); }
  const who = String(body.who || "").trim().slice(0, 80);
  if (!who) return json({ ok: false, error: "no_name" }, 400);

  // только валидные id и непустые значения; жёсткие потолки против мусора
  const clean = (src, isStatus) => {
    const out = {};
    let n = 0;
    for (const k of Object.keys(src || {})) {
      if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,23}$/.test(k)) continue;
      const v = String(src[k]).trim().slice(0, 4000);
      if (!v) continue;
      if (isStatus && !ST_ALLOWED.has(v)) continue;
      out[k] = v;
      if (++n >= 400) break;
    }
    return out;
  };
  const ans = clean(body.ans), eng = clean(body.eng), st = clean(body.st, true);
  const total = Object.keys(ans).length + Object.keys(eng).length + Object.keys(st).length;
  if (!total) return json({ ok: false, error: "empty" }, 400);

  // две попытки: между GET и PUT кто-то мог закоммитить свои ответы (409 по sha)
  for (let attempt = 0; attempt < 2; attempt++) {
    let cur;
    try { cur = await ghGetFile(env); } catch (e) { return json({ ok: false, error: String(e.message || e) }, 502); }
    const d = cur.data;
    d.ans = { ...(d.ans || {}), ...ans };
    d.eng = { ...(d.eng || {}), ...eng };
    d.st = { ...(d.st || {}), ...st };
    d.meta = d.meta || {};
    const today = new Date().toISOString().slice(0, 10);
    for (const k of new Set([...Object.keys(ans), ...Object.keys(eng), ...Object.keys(st)])) {
      d.meta[k] = { by: who, at: today };
    }
    d.updated = today;
    const res = await ghPutFile(env, d, cur.sha, who, total);
    if (res.ok) {
      const j = await res.json();
      return json({ ok: true, commit: ((j.commit && j.commit.sha) || "").slice(0, 7), total });
    }
    if (res.status !== 409) return json({ ok: false, error: "github_" + res.status }, 502);
  }
  return json({ ok: false, error: "conflict" }, 502);
}

function ghHeaders(env) {
  return {
    "Authorization": `Bearer ${env.GH_ANSWERS_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "User-Agent": "kvant-ove-answers",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

async function ghGetFile(env) {
  const r = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_PATH}?ref=${GH_BRANCH}`,
    { headers: ghHeaders(env) });
  if (r.status === 404) {
    // GitHub отдаёт 404 и когда файла нет, и когда у токена нет доступа к
    // репозиторию. Разница критическая: пустой документ отсюда уходит в
    // ghPutFile без sha, то есть файл создаётся заново — и все накопленные
    // ответы заказчика затираются. Различаем запросом самого репозитория.
    const probe = await fetch(`https://api.github.com/repos/${GH_REPO}`, { headers: ghHeaders(env) });
    if (!probe.ok) throw new Error("github_no_access_" + probe.status);
    return { sha: undefined, data: { updated: "", note: "", ans: {}, eng: {}, st: {}, meta: {} } };
  }
  if (!r.ok) throw new Error("github_get_" + r.status);
  const j = await r.json();
  return { sha: j.sha, data: JSON.parse(b64decodeUtf8(j.content)) };
}

function ghPutFile(env, data, sha, who, total) {
  const body = {
    message: `ОВЭ-75: ответы и комментарии по вопросам — ${who} (+${total})`,
    content: b64encodeUtf8(JSON.stringify(data, null, 1) + "\n"),
    branch: GH_BRANCH,
    committer: { name: "OVE-75 answers", email: "ove-answers@users.noreply.github.com" },
    author: { name: who, email: "ove-answers@users.noreply.github.com" },
  };
  if (sha) body.sha = sha;
  return fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_PATH}`,
    { method: "PUT", headers: { ...ghHeaders(env), "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

// base64 для UTF-8: btoa/atob сами по себе кириллицу не переваривают
function b64encodeUtf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}
function b64decodeUtf8(b64) {
  const bin = atob(String(b64).replace(/\s+/g, ""));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
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

const SITE = "ove";
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

// экспорт для тестов (на исполнение воркера не влияет)
export { ghGetFile };
