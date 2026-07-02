// Гейт доступа к дашборду: HTTP Basic Auth перед отдачей статики + САМООБНОВЛЕНИЕ.
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы
// через этот fetch; сами файлы отдаём через env.ASSETS уже после проверки.
//
// Пароль в КОДЕ НЕ хранится — берётся из переменной окружения проекта
// BASIC_AUTH_PASS (секрет Cloudflare Pages). Логин — BASIC_AUTH_USER (по умолчанию "kvant").
//
// САМООБНОВЛЕНИЕ: если при заходе данные старше 2 ч, воркер сам триггерит пересборку
// (GitHub repository_dispatch {"event_type":"rebuild"}), а страница опрашивает сервер
// и перезагрузится, когда придёт свежий деплой. Так датчик почти всегда зелёный.
// Нужен один секрет проекта: GH_DISPATCH_TOKEN — fine-grained PAT этого репозитория
// с правом Contents: Read and write (или classic с scope `repo`). Без него воркер просто
// отдаёт статику как раньше (самообновление выключено, ломаться нечему).
const GH_REPO = "skharkov1246/kvant-sourcing-dashboard";
const FRESH_MS = 2 * 3600 * 1000;          // порог свежести — 2 часа
const DEBOUNCE_MS = 15 * 60;               // не триггерить пересборку чаще раза в 15 мин (сек, для Cache-Control)

export default {
  async fetch(request, env, ctx) {
    const user = env.BASIC_AUTH_USER || "kvant";
    const pass = env.BASIC_AUTH_PASS;

    // секрет не задан → доступ закрыт (fail-closed), с подсказкой по настройке
    if (!pass) {
      return new Response(
        "Доступ не настроен: задайте секрет BASIC_AUTH_PASS в проекте Cloudflare Pages.",
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }

    const got = request.headers.get("Authorization") || "";
    const want = "Basic " + btoa(`${user}:${pass}`);
    if (!timingSafeEqual(got, want)) {
      return new Response("Требуется авторизация", {
        status: 401,
        headers: {
          "WWW-Authenticate": 'Basic realm="Sourcing Dashboard · КВАНТ", charset="UTF-8"',
          "Cache-Control": "no-store",
        },
      });
    }

    const url = new URL(request.url);

    // /gen — лёгкая проверка свежести для поллинга со страницы: вместо скачивания
    // всего index.html клиент получает пару десятков байт JSON с меткой генерации.
    // Заодно триггерит пересборку, если данные устарели (как заход на страницу).
    if (url.pathname === "/gen") {
      const idx = await env.ASSETS.fetch(new Request(url.origin + "/", { headers: request.headers }));
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

    // авторизованы → отдаём статический файл, но ЗАПРЕЩАЕМ кэширование:
    // иначе браузер/edge отдают старый index.html и после деплоя «сайт не обновляется».
    const resp = await env.ASSETS.fetch(request);
    const headers = new Headers(resp.headers);
    headers.set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
    headers.set("Pragma", "no-cache");
    headers.set("Expires", "0");

    const ctype = resp.headers.get("content-type") || "";
    if (ctype.includes("text/html")) {
      // лог визита для ежедневного отчёта (уникальные IP). Поллинг свежести (X-Poll) не считаем.
      if (env.VISITS && request.headers.get("X-Poll") !== "1") ctx.waitUntil(logVisit(request, env));
      try {
        // читаем БАЙТЫ один раз; их же и отдаём (без двойного чтения/перекодировки).
        // Content-Encoding/Length чистим — тело уже декодировано рантаймом, edge сожмёт заново.
        const buf = await resp.arrayBuffer();
        const text = new TextDecoder().decode(buf);
        const m = text.match(/gen=new Date\("([^"]+)"\)/);
        const genMs = m ? Date.parse(m[1]) : NaN;
        const stale = isNaN(genMs) || (Date.now() - genMs) > FRESH_MS;
        if (stale && env.GH_DISPATCH_TOKEN) ctx.waitUntil(triggerRebuild(env));
        headers.delete("Content-Encoding");
        headers.delete("Content-Length");
        return new Response(buf, { status: resp.status, statusText: resp.statusText, headers });
      } catch (e) {
        // что-то пошло не так с чтением — отдаём страницу как есть, ничего не ломаем
        return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
      }
    }
    return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers });
  },
};

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

// сравнение за постоянное время, чтобы не подсказывать пароль по таймингу
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
