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

    if (!pass) {
      return new Response(
        "Доступ не настроен: задайте секрет BASIC_AUTH_PASS в проекте Cloudflare Pages «kvant-ove».",
        { status: 503, headers: { "Cache-Control": "no-store" } },
      );
    }

    const got = request.headers.get("Authorization") || "";
    const want = "Basic " + btoa(`${user}:${pass}`);
    if (!timingSafeEqual(got, want)) {
      return new Response("Требуется авторизация", {
        status: 401,
        headers: {
          "WWW-Authenticate": 'Basic realm="ОВЭ-75 · КВАНТ", charset="UTF-8"',
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
