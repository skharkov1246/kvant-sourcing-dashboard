// Гейт доступа к дашборду: HTTP Basic Auth перед отдачей статики.
// Cloudflare Pages в advanced-режиме (наличие _worker.js) гоняет ВСЕ запросы
// через этот fetch; сами файлы отдаём через env.ASSETS уже после проверки.
//
// Пароль в КОДЕ НЕ хранится — берётся из переменной окружения проекта
// BASIC_AUTH_PASS (секрет Cloudflare Pages; задаётся в CI или в дашборде).
// Логин — BASIC_AUTH_USER (по умолчанию "kvant").
export default {
  async fetch(request, env) {
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
    // авторизованы → отдаём статический файл из public/
    return env.ASSETS.fetch(request);
  },
};

// сравнение за постоянное время, чтобы не подсказывать пароль по таймингу
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
