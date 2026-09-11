// База ЗИП ходит через собственный воркер: /db/* → Supabase, ключ подставляет
// воркер уже после проверки входа Access. Раньше ключ лежал в отдаваемой странице,
// и любой, кто её открыл, мог обращаться к базе напрямую — мимо сайта и мимо Access.
// Тесты закрепляют: адрес собирается верно, ключ из браузера не проходит, куки
// Access наружу не уходят, и обращения к /db/ не засоряют журнал действий.
// Запуск: node --test access/test/zip_db_proxy.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { proxyDb, SUPA_ORIGIN } from "../../zip/site/_worker.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

let seen;
const realFetch = globalThis.fetch;
function stub(status = 200, body = "[]") {
  seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), init });
    return new Response(body, { status, headers: { "Content-Type": "application/json" } });
  };
}
test.after(() => { globalThis.fetch = realFetch; });

const req = (url, init) => new Request(url, init);

test("путь и строка запроса переносятся в Supabase без /db", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records?select=*&limit=5"), {});
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, SUPA_ORIGIN + "/rest/v1/price_records?select=*&limit=5");
});

test("ключ ставит воркер: секрет Cloudflare сильнее того, что прислал браузер", async () => {
  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/drawings", {
      headers: { apikey: "via-worker", Authorization: "Bearer via-worker" },
    }),
    { SUPABASE_SERVICE_KEY: "secret-from-cloudflare" },
  );
  const h = seen[0].init.headers;
  assert.equal(h.get("apikey"), "secret-from-cloudflare");
  assert.equal(h.get("Authorization"), "Bearer secret-from-cloudflare");
});

test("пока секрета нет — прежний публикуемый ключ, сайт не ломается", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/drawings"), {});
  assert.match(seen[0].init.headers.get("apikey"), /^sb_publishable_/);
});

test("куки Access и служебные заголовки Cloudflare в базу не уходят", async () => {
  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/samples", {
      headers: {
        Cookie: "CF_Authorization=jwt-of-employee",
        "Cf-Access-Jwt-Assertion": "jwt-of-employee",
        "Cf-Connecting-Ip": "203.0.113.7",
        "X-Forwarded-For": "203.0.113.7",
      },
    }),
    {},
  );
  const h = seen[0].init.headers;
  for (const name of ["cookie", "cf-access-jwt-assertion", "cf-connecting-ip", "x-forwarded-for"]) {
    assert.equal(h.get(name), null, `заголовок ${name} должен быть снят`);
  }
});

test("тело передаётся только там, где оно есть", async () => {
  stub();
  await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(seen[0].init.body, undefined);
  assert.equal(seen[0].init.method, "GET");

  stub();
  await proxyDb(
    req("https://kvant-zip.pages.dev/db/rest/v1/price_records", { method: "POST", body: '{"pn":"1"}' }),
    {},
  );
  assert.equal(seen[0].init.method, "POST");
  assert.notEqual(seen[0].init.body, undefined);
});

test("ответ базы не кэшируется и не проносит set-cookie", async () => {
  stub();
  const out = await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(out.headers.get("Cache-Control"), "no-store");
  assert.equal(out.headers.get("set-cookie"), null);
});

test("код ответа базы доходит до браузера как есть", async () => {
  stub(401, '{"message":"Invalid API key"}');
  const out = await proxyDb(req("https://kvant-zip.pages.dev/db/rest/v1/price_records"), {});
  assert.equal(out.status, 401);
});

test("страница ЗИП больше не содержит ключ Supabase", () => {
  // zip/public/ — результат сборки и в git не хранится (.gitignore), поэтому
  // проверяем его только там, где он собран локально.
  for (const f of ["zip/site/index.template.html", "zip/public/index.html"]) {
    const p = path.join(ROOT, f);
    if (!fs.existsSync(p)) continue;
    const s = fs.readFileSync(p, "utf8");
    assert.ok(!/sb_publishable_[A-Za-z0-9_-]/.test(s), `ключ остался в ${f}`);
    assert.ok(!/vpjliavuuxjcvtxbthlp\.supabase\.co/.test(s), `адрес базы остался в ${f}`);
  }
});

test("обращения страницы к данным не идут в журнал действий", async () => {
  const { AUDIT_SKIP } = await import("../siterights.js");
  assert.ok(AUDIT_SKIP.test("/db/rest/v1/price_records"), "/db/ должен пропускаться журналом");
  assert.ok(AUDIT_SKIP.test("/db/storage/v1/object/drawings/a.pdf"), "файлы базы — тоже не действие");
  assert.ok(!AUDIT_SKIP.test("/orders/spec.pdf"), "выгрузка документа остаётся в журнале");
  assert.ok(!AUDIT_SKIP.test("/gt/"), "открытие страницы остаётся в журнале");
});
