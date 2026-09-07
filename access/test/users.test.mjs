// Тест именного входа + сверка, что копии помощника в четырёх гейтах не разошлись
// с каноническим access/users.js. Запуск: node --test access/test/users.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { userOk, sha256Hex } from "../users.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const GATES = ["public/_worker.js", "gpu/public/_worker.js", "ove/public/_worker.js", "zip/site/_worker.js.example",
  "gidromet/public/_worker.js",
  "factory/public/_worker.js",
];

const req = (login, pass) => new Request("https://x/", {
  headers: login == null ? {} : { Authorization: "Basic " + Buffer.from(`${login}:${pass}`, "utf8").toString("base64") },
});
async function table(...rows) {
  const out = ["# тестовая таблица"];
  for (const [email, pass, sites] of rows) out.push(`${email} ${await sha256Hex(`${email}:${pass}`)} ${sites ?? "*"}`);
  return out.join("\n");
}

test("сотрудник входит по своей почте и паролю; чужой пароль не подходит", async () => {
  const env = { BASIC_AUTH_USERS: await table(["ivanov@kvant.ru", "Aq7d-9Kmp-2XwR"]) };
  assert.equal(await userOk(req("ivanov@kvant.ru", "Aq7d-9Kmp-2XwR"), env, "sourcing"), true);
  assert.equal(await userOk(req("IVANOV@kvant.ru", "Aq7d-9Kmp-2XwR"), env, "zip"), true, "регистр почты не важен");
  assert.equal(await userOk(req("ivanov@kvant.ru", "wrong"), env, "sourcing"), false);
  assert.equal(await userOk(req("petrov@kvant.ru", "Aq7d-9Kmp-2XwR"), env, "sourcing"), false);
  assert.equal(await userOk(req(null), env, "sourcing"), false);
});

test("права по сайтам: список сайтов и звёздочка", async () => {
  const env = { BASIC_AUTH_USERS: await table(
    ["a@kvant.ru", "p1", "sourcing,zip"], ["b@kvant.ru", "p2", "*"]) };
  assert.equal(await userOk(req("a@kvant.ru", "p1"), env, "sourcing"), true);
  assert.equal(await userOk(req("a@kvant.ru", "p1"), env, "zip"), true);
  assert.equal(await userOk(req("a@kvant.ru", "p1"), env, "gpu"), false, "gpu не выдан");
  assert.equal(await userOk(req("b@kvant.ru", "p2"), env, "gpu"), true);
});

test("резервный общий пароль работает только под своим логином", async () => {
  const env = { BASIC_AUTH_USERS: await table(["a@kvant.ru", "p1"]), BASIC_AUTH_PASS: "master" };
  assert.equal(await userOk(req("kvant", "master"), env, "sourcing"), true);
  assert.equal(await userOk(req("a@kvant.ru", "master"), env, "sourcing"), false, "общий пароль под чужой почтой");
  assert.equal(await userOk(req("kvant", "master"), { BASIC_AUTH_PASS: "master" }, "ove"), true, "таблицы нет — старое поведение");
  assert.equal(await userOk(req("kvant", "master"), {}, "ove"), false, "без секретов вход закрыт");
});

test("пустые строки, комментарии и пробелы в таблице не ломают разбор", async () => {
  const h = await sha256Hex("z@kvant.ru:pw");
  const env = { BASIC_AUTH_USERS: `\n# люди\n\n  z@kvant.ru   ${h.toUpperCase()}   sourcing , ove \n` };
  assert.equal(await userOk(req("z@kvant.ru", "pw"), env, "ove"), true);
});

test("копии помощника в гейтах совпадают с каноническим", () => {
  const canon = fs.readFileSync(path.join(ROOT, "access/users.js"), "utf8");
  const block = (s) => {
    const m = s.match(/\/\/ BEGIN userOk[\s\S]*?\/\/ END userOk/);
    return m ? m[0] : null;
  };
  const want = block(canon);
  assert.ok(want, "в access/users.js нет маркеров");
  for (const g of GATES) {
    const got = block(fs.readFileSync(path.join(ROOT, g), "utf8"));
    assert.equal(got, want, `${g}: блок userOk разошёлся с access/users.js`);
    assert.match(fs.readFileSync(path.join(ROOT, g), "utf8"), /const SITE = "(sourcing|gpu|ove|zip|gidromet|gok)";/, `${g}: нет константы SITE`);
  }
});
