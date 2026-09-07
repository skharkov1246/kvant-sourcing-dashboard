// Гейт доступа к сайту ОВЭ-75: HTTP Basic Auth перед отдачей статики.
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы
// через этот fetch; файлы отдаём через env.ASSETS уже после проверки.
//
// Пароль в КОДЕ НЕ хранится — берётся из переменной окружения проекта
// BASIC_AUTH_PASS (секрет Cloudflare Pages проекта kvant-ove).
// Логин — BASIC_AUTH_USER (по умолчанию "kvant").
//
// Поведение без секрета — fail-closed: сайт отдаёт 503 и никого не пускает.
// Это осознанно: внутри тендерные данные КГМК/Гипроникеля, наш пул поставщиков
// со статусами запросов и досье на конкурента. Пустой пароль здесь опаснее,
// чем недоступный сайт.
//
// Самообновления и счётчика визитов тут намеренно нет — это статический
// разбор тендерного пакета, а не живой дашборд.

// ── Приём ответов с вкладки «Вопросы Заказчику» ──────────────────────────────
// POST /api/answers коммитит ответы Заказчика и комментарии инженеров в
// ove/data/questions_answers.json ветки main через GitHub Contents API.
// Каждая отправка = настоящий git-коммит с именем инженера в авторе; пуш в
// main триггерит ove-deploy, и через ~3 минуты ответы запечены в страницу для
// всей команды. Нужен секрет проекта GH_ANSWERS_TOKEN — fine-grained PAT этого
// репозитория с правом Contents: Read and write. Путь файла зашит в код:
// токен шире, но воркер физически не умеет писать никуда, кроме этого файла.
// Отправка требует Basic Auth: пока пароль сайта не задан (временное открытое
// окно), эндпоинт отвечает 503 — анонимные коммиты в репозиторий недопустимы.

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/answers") return handleAnswers(request, env);

    const user = env.BASIC_AUTH_USER || "kvant";
    const pass = env.BASIC_AUTH_PASS;

    // ⚠ ВРЕМЕННЫЙ РЕЖИМ, распоряжение владельца 13.08.2026: «выкладывай пока всё
    // без пароля, запаролю утром завтра». Пока секрет BASIC_AUTH_PASS не задан —
    // сайт открыт. Как только владелец добавит секрет в Cloudflare, гейт включается
    // сам: ни правок в коде, ни пересборки не нужно.
    //
    // Это fail-OPEN, и как постоянное решение он опасен: если секрет когда-нибудь
    // удалят, сайт молча откроется. После того как владелец подтвердит, что пароль
    // задан и вход работает, вернуть здесь fail-closed (отдавать 503 без секрета).
    if (!pass) {
      const resp = await env.ASSETS.fetch(request);
      const open = new Response(resp.body, resp);
      open.headers.set("Cache-Control", "no-store");
      open.headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
      open.headers.set("Referrer-Policy", "no-referrer");
      return open;
    }
    if (!basicOk(request, user, pass)) {
      return new Response(page(
        "ОВЭ-75 · КВАНТ",
        "<p>Материалы проекта «обжиг – выщелачивание – электроэкстракция» для АО «Кольская ГМК».</p>" +
        "<p>Введите логин и пароль в окне браузера. Если окно не появилось — обновите страницу. " +
        "Логин и пароль выдаёт владелец.</p>"),
        {
          status: 401,
          headers: {
            // realm только ASCII: значения HTTP-заголовков — ByteString (Latin-1),
            // кириллица здесь роняет ответ и окно ввода пароля не появляется
            "WWW-Authenticate": 'Basic realm="OVE-75 KVANT", charset="UTF-8"',
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
          },
        });
    }

    const resp = await env.ASSETS.fetch(request);
    const out = new Response(resp.body, resp);
    // страницу не кэшируем: данные обновляются пересборкой, а закэшированная
    // копия у прокси пережила бы смену пароля
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

  const user = env.BASIC_AUTH_USER || "kvant";
  const pass = env.BASIC_AUTH_PASS;
  // без пароля сайта отправка выключена: иначе кто угодно коммитил бы в репозиторий
  if (!pass) return json({ ok: false, error: "submit_disabled_no_password" }, 503);
  if (!basicOk(request, user, pass)) {
    return json({ ok: false, error: "auth" }, 401,
      { "WWW-Authenticate": 'Basic realm="OVE-75 KVANT", charset="UTF-8"' });
  }
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
  if (r.status === 404) return { sha: undefined, data: { updated: "", note: "", ans: {}, eng: {}, st: {}, meta: {} } };
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

// служебная страница в стиле сайта: светлая и тёмная тема, без внешних ресурсов
function page(title, body) {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title>
<style>:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--b:rgba(11,11,11,.10);--s1:#2a78d6}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--b:rgba(255,255,255,.10);--s1:#3987e5}}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;
background:var(--page);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.c{background:var(--surface);border:1px solid var(--b);border-left:4px solid var(--s1);border-radius:12px;
padding:22px 26px;max-width:620px}h1{font-size:19px;margin:0 0 10px}p{margin:9px 0;color:var(--ink2)}
code{background:var(--page);border:1px solid var(--b);border-radius:5px;padding:1px 5px;font-size:13px;
overflow-wrap:anywhere}b{color:var(--ink)}</style></head>
<body><div class="c"><h1>${title}</h1>${body}</div></body></html>`;
}

// сравнение за постоянное время — чтобы по времени ответа нельзя было подбирать пароль
function timingSafeEqual(a, b) {
  const ea = new TextEncoder().encode(a);
  const eb = new TextEncoder().encode(b);
  if (ea.length !== eb.length) {
    // всё равно проходим цикл, чтобы длина не утекала через тайминг
    let d = 1;
    for (let i = 0; i < Math.max(ea.length, eb.length); i++) d |= (ea[i] ?? 0) ^ (eb[i] ?? 1);
    return false;
  }
  let diff = 0;
  for (let i = 0; i < ea.length; i++) diff |= ea[i] ^ eb[i];
  return diff === 0;
}

// Разбор заголовка Basic без btoa: пароль может содержать любые символы UTF-8.
// btoa на строке с кириллицей бросает исключение, и сайт отвечает 500 на каждый
// запрос — то есть падает целиком. Здесь декодируем присланные байты и сравниваем
// уже строки, поэтому пароль может быть любым.
function parseBasic(header) {
  if (!header || !header.startsWith("Basic ")) return null;
  let raw;
  try { raw = atob(header.slice(6)); } catch { return null; }
  const text = new TextDecoder().decode(Uint8Array.from(raw, (c) => c.charCodeAt(0)));
  const i = text.indexOf(":");
  return i < 0 ? null : { user: text.slice(0, i), pass: text.slice(i + 1) };
}

// сравнение без ранних выходов (не зависит от позиции первого несовпадения)
function safeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(String(a)), y = enc.encode(String(b));
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}

function basicOk(request, user, pass) {
  const creds = parseBasic(request.headers.get("Authorization"));
  return !!creds && safeEqual(creds.user, user) && safeEqual(creds.pass, pass);
}
