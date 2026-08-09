// ЛОКАЛЬНЫЙ ТЕСТОВЫЙ КОНТУР доступов. Ничего не деплоит, в прод не ходит.
//   node access/devserver.mjs            → http://127.0.0.1:8788
//   node access/devserver.mjs --reset    → очистить состояние контура
//
// Что можно прокликать:
//   /access/          — форма заявки (галочки вкладок) и вход по ссылке
//   /access/outbox    — «почтовый ящик» контура: там лежат все письма со ссылками
//   /admin            — админка (вход по Basic Auth kvant / contour, либо magic-link владельца)
//   /                 — сам дашборд, порезанный по правам вошедшего
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { handle } from "./gate.js";
import { DIR, ROOT, buildIndex, testEnv } from "./contour.mjs";

const PORT = parseInt(process.env.PORT || "8788", 10);
const STATE = path.join(DIR, ".contour");
if (process.argv.includes("--reset") && fs.existsSync(STATE)) fs.rmSync(STATE, { recursive: true, force: true });
fs.mkdirSync(STATE, { recursive: true });

const assetsDir = path.join(STATE, "public");
const built = buildIndex(assetsDir);
// шрифты — символьной ссылкой, чтобы дашборд выглядел как настоящий
const fontsSrc = path.join(ROOT, "public", "fonts");
const fontsDst = path.join(assetsDir, "fonts");
if (fs.existsSync(fontsSrc) && !fs.existsSync(fontsDst)) {
  try { fs.symlinkSync(fontsSrc, fontsDst, "dir"); } catch { /* не критично */ }
}

const env = testEnv({ kvFile: path.join(STATE, "kv.json"), assetsDir });
env.SITE_URL = process.env.SITE_URL || `http://127.0.0.1:${PORT}`;

const server = http.createServer(async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const request = new Request(`http://127.0.0.1:${PORT}${req.url}`, {
    method: req.method,
    headers: Object.entries(req.headers).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]),
    body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
    redirect: "manual",
  });
  let resp;
  try {
    resp = await handle(request, env, { waitUntil: (p) => p });
  } catch (e) {
    console.error(e);
    resp = new Response("Ошибка контура:\n" + (e && e.stack || e), { status: 500 });
  }
  const headers = {};
  for (const [k, v] of resp.headers) headers[k] = k === "set-cookie" ? v : v;
  res.writeHead(resp.status, headers);
  res.end(Buffer.from(await resp.arrayBuffer()));
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`\n🧪 Тестовый контур доступов запущен`);
  console.log(`   дашборд:        http://127.0.0.1:${PORT}/`);
  console.log(`   заявка/вход:    http://127.0.0.1:${PORT}/access/`);
  console.log(`   ящик писем:     http://127.0.0.1:${PORT}/access/outbox`);
  console.log(`   админка:        http://127.0.0.1:${PORT}/admin   (Basic Auth: kvant / contour)`);
  console.log(`   index.html:     ${built.source}`);
  console.log(`   состояние:      ${path.join(STATE, "kv.json")}  (сброс: --reset)\n`);
});
