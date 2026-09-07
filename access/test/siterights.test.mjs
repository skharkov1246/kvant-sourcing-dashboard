// Право на конкретный сайт: гейт спрашивает портал, переслав подпись входа сотрудника.
// Запуск: node --test access/test/siterights.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { siteAllowed, RIGHTS_URL, rightsCache } from "../siterights.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const GATES = ["gpu/public/_worker.js", "ove/public/_worker.js", "zip/site/_worker.js",
               "gidromet/public/_worker.js", "factory/public/_worker.js"];

const req = (jwt) => new Request("https://kvant-gpu.pages.dev/", jwt ? { headers: { "Cf-Access-Jwt-Assertion": jwt } } : {});
const reqCookie = (jwt) => new Request("https://kvant-gpu.pages.dev/", { headers: { Cookie: `CF_Authorization=${jwt}` } });

// Подменяем сеть: запоминаем, что и куда ушло.
let calls, reply;
const realFetch = globalThis.fetch;
function stub() {
  calls = [];
  globalThis.fetch = async (url, init) => { calls.push({ url: String(url), init }); return reply(); };
}
test.after(() => { globalThis.fetch = realFetch; });

test("сайт из списка прав открывается, отсутствующий — нет", async () => {
  rightsCache.clear(); stub();
  reply = () => new Response(JSON.stringify({ email: "a@kvantpro.com", sites: ["dashboard", "gpu"] }),
    { headers: { "Content-Type": "application/json" } });
  assert.equal(await siteAllowed(req("jwt-a"), {}, "gpu"), true);
  rightsCache.clear();
  assert.equal(await siteAllowed(req("jwt-a"), {}, "gok"), false);
  assert.equal(calls[0].url, RIGHTS_URL);
  assert.equal(calls[0].init.headers["Cf-Access-Jwt-Assertion"], "jwt-a", "подпись входа пересылается порталу");
});

test("подпись берётся и из куки", async () => {
  rightsCache.clear(); stub();
  reply = () => new Response(JSON.stringify({ sites: [] }), { headers: { "Content-Type": "application/json" } });
  assert.equal(await siteAllowed(reqCookie("jwt-c"), {}, "gpu"), false);
  assert.equal(calls[0].init.headers["Cf-Access-Jwt-Assertion"], "jwt-c");
});

test("портал недоступен или ответил невнятно — сайт не запирается", async () => {
  for (const bad of [
    () => { throw new Error("сеть"); },
    () => new Response("", { status: 502 }),
    () => new Response("не json", { headers: { "Content-Type": "application/json" } }),
    () => new Response(JSON.stringify({ error: "х" }), { headers: { "Content-Type": "application/json" } }),
  ]) {
    rightsCache.clear(); stub(); reply = bad;
    assert.equal(await siteAllowed(req("jwt-x"), {}, "gok"), true);
  }
});

test("без подписи входа проверка не вмешивается — периметр держит Access", async () => {
  rightsCache.clear(); stub(); reply = () => new Response("{}");
  assert.equal(await siteAllowed(req(null), {}, "gok"), true);
  assert.equal(calls.length, 0, "лишнего запроса к порталу нет");
});

test("выключатель SITE_RIGHTS=off пропускает всех", async () => {
  rightsCache.clear(); stub(); reply = () => new Response("{}");
  assert.equal(await siteAllowed(req("jwt"), { SITE_RIGHTS: "off" }, "gok"), true);
  assert.equal(calls.length, 0);
});

test("ответ помнится минуту: портал не дёргается на каждый файл", async () => {
  rightsCache.clear(); stub();
  reply = () => new Response(JSON.stringify({ sites: ["gpu"] }), { headers: { "Content-Type": "application/json" } });
  await siteAllowed(req("jwt-m"), {}, "gpu");
  await siteAllowed(req("jwt-m"), {}, "gpu");
  assert.equal(calls.length, 1, "второй запрос взят из памяти изолята");
  assert.equal(await siteAllowed(req("jwt-m"), {}, "gok"), false, "из памяти отвечаем и по другому сайту");
  assert.equal(calls.length, 1);
  await siteAllowed(req("jwt-other"), {}, "gpu");
  assert.equal(calls.length, 2, "для другого человека права спрашиваются заново");
});

test("копии блока в пяти гейтах совпадают с каноническим access/siterights.js", () => {
  const block = (s) => { const m = s.match(/\/\/ BEGIN siteRights[\s\S]*?\/\/ END siteRights/); return m ? m[0] : null; };
  const want = block(fs.readFileSync(path.join(ROOT, "access/siterights.js"), "utf8"));
  assert.ok(want, "в access/siterights.js нет маркеров");
  for (const g of GATES) {
    const src = fs.readFileSync(path.join(ROOT, g), "utf8");
    assert.equal(block(src), want, `${g}: блок siteRights разошёлся с access/siterights.js`);
    const site = (src.match(/const SITE = "([a-z]+)";/) || [])[1];
    assert.ok(site, `${g}: нет константы SITE`);
    assert.match(src, /siteAllowed\(request, env, /, `${g}: право на сайт не проверяется`);
  }
});

test("гейт ГШО спрашивает право на ГТУ для раздела /gt/", () => {
  const src = fs.readFileSync(path.join(ROOT, "zip/site/_worker.js"), "utf8");
  assert.match(src, /path === "\/gt" \|\| path\.startsWith\("\/gt\/"\)/, "раздел ГТУ не отделён");
  assert.match(src, /siteAllowed\(request, env, gt \? "gt" : SITE\)/, "право на ГТУ не проверяется отдельно");
});

test("идентификаторы сайтов в гейтах есть в справочнике прав", async () => {
  const { SITE_IDS } = await import("../acl.js");
  for (const g of GATES) {
    const src = fs.readFileSync(path.join(ROOT, g), "utf8");
    const site = src.match(/const SITE = "([a-z]+)";/)[1];
    assert.ok(SITE_IDS.includes(site), `${g}: сайт «${site}» не описан в access/acl.js`);
  }
});
