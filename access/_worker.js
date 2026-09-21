// Точка входа Cloudflare Pages для КОНТУРА ДОСТУПОВ.
// Прод пока живёт на public/_worker.js (Basic Auth) и этим файлом не затрагивается.
// Здесь только тонкая обёртка: вся логика — в gate.js, чтобы её можно было
// прогонять локально (access/devserver.mjs, access/test/flow.test.mjs).
import { handle } from "./gate.js";

export default {
  async fetch(request, env, ctx) {
    try {
      return await handle(request, env, ctx);
    } catch (e) {
      // гейт падать не должен: если упал — закрываемся, а не открываемся
      return new Response("Гейт доступа временно недоступен.\n" + (e && e.stack || e), {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
      });
    }
  },
};
