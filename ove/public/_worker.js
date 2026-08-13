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

export default {
  async fetch(request, env) {
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

    const got = request.headers.get("Authorization") || "";
    const want = "Basic " + btoa(`${user}:${pass}`);
    if (!timingSafeEqual(got, want)) {
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
