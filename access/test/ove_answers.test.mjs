// Форма ответов ОВЭ-75 пишет их прямо в репозиторий через GitHub API.
// GitHub отдаёт 404 и когда файла нет, и когда у токена нет доступа. Раньше оба
// случая давали пустой документ, а он уходил в запись без sha — то есть файл
// создавался заново и все накопленные ответы затирались. Тест закрепляет разницу.
// Запуск: node --test access/test/ove_answers.test.mjs
import test from "node:test";
import assert from "node:assert/strict";

import { ghGetFile } from "../../ove/public/_worker.js";

const env = { GH_ANSWERS_TOKEN: "t" };
const json = (body) => ({ status: 200, ok: true, json: async () => body });

function mockFetch(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    for (const [needle, resp] of routes) {
      if (String(url).includes(needle)) return resp;
    }
    throw new Error("неожиданный запрос: " + url);
  };
  return calls;
}

test("нет доступа к репозиторию: ошибка, а не пустой документ", async () => {
  mockFetch([
    ["/contents/", { status: 404, ok: false }],
    ["/repos/skharkov1246/kvant-sourcing-dashboard", { status: 404, ok: false }],
  ]);
  await assert.rejects(() => ghGetFile(env), /github_no_access_404/,
    "при потере доступа форма обязана падать, иначе следующая запись сотрёт ответы");
});

test("файла ещё нет, но репозиторий доступен: пустой документ — это норма", async () => {
  mockFetch([
    ["/contents/", { status: 404, ok: false }],
    ["/repos/skharkov1246/kvant-sourcing-dashboard", { status: 200, ok: true }],
  ]);
  const { sha, data } = await ghGetFile(env);
  assert.equal(sha, undefined);
  assert.deepEqual(data.ans, {});
});

test("файл есть: возвращается его sha, запись пойдёт поверх существующего", async () => {
  const content = Buffer.from(JSON.stringify({ updated: "2026-09-01", ans: { q1: "да" } }), "utf8").toString("base64");
  mockFetch([["/contents/", json({ sha: "abc123", content })]]);
  const { sha, data } = await ghGetFile(env);
  assert.equal(sha, "abc123");
  assert.equal(data.ans.q1, "да");
});
