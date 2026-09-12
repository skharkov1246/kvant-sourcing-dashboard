// Тест помощника входа через Cloudflare Access и сверка его копий во всех гейтах.
// Запуск: node --test access/test/cfaccess.test.mjs
// Ключи: пара RSA генерируется на лету, «сертификаты команды» подставляются через env.__certs.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { accessOk } from "../cfaccess.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const GATES = [
  "public/_worker.js",
  "gpu/public/_worker.js",
  "ove/public/_worker.js",
  "zip/site/_worker.js",
  "gidromet/public/_worker.js",
  "factory/public/_worker.js",
];
const TEAM = "test-team.cloudflareaccess.com";

const b64u = (buf) => Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
let priv, jwk;
async function keys() {
  if (priv) return;
  const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  priv = kp.privateKey;
  jwk = { ...(await crypto.subtle.exportKey("jwk", kp.publicKey)), kid: "k1", use: "sig", alg: "RS256" };
}
async function token(payload, { kid = "k1", signer = null } = {}) {
  await keys();
  const h = b64u(JSON.stringify({ alg: "RS256", kid }));
  const p = b64u(JSON.stringify(payload));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", signer || priv, new TextEncoder().encode(`${h}.${p}`));
  return `${h}.${p}.${b64u(sig)}`;
}
const now = () => Math.floor(Date.now() / 1000);
const good = () => ({ email: "Ivanov@KvantPro.com", sub: "u1", iss: `https://${TEAM}`, aud: ["aud-1"], exp: now() + 600, iat: now() });
const env = () => ({ CF_ACCESS_TEAM: TEAM, __certs: { keys: [jwk] } });
const reqH = (jwt) => new Request("https://x/", { headers: jwt ? { "Cf-Access-Jwt-Assertion": jwt } : {} });
const reqC = (jwt) => new Request("https://x/", { headers: { Cookie: `a=1; CF_Authorization=${jwt}; b=2` } });

test("действительный вход принимается, почта нормализуется", async () => {
  await keys();
  const who = await accessOk(reqH(await token(good())), env());
  assert.deepEqual(who && who.email, "ivanov@kvantpro.com");
  assert.equal(who.sub, "u1");
});

test("вход из куки CF_Authorization", async () => {
  const who = await accessOk(reqC(await token(good())), env());
  assert.equal(who && who.email, "ivanov@kvantpro.com");
});

test("без токена — отказ", async () => {
  assert.equal(await accessOk(reqH(null), env()), null);
});

test("подделанная подпись — отказ", async () => {
  const other = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const forged = await token(good(), { signer: other.privateKey });
  assert.equal(await accessOk(reqH(forged), env()), null);
});

test("истёкший, чужой издатель, неизвестный ключ — отказ", async () => {
  assert.equal(await accessOk(reqH(await token({ ...good(), exp: now() - 120 })), env()), null);
  assert.equal(await accessOk(reqH(await token({ ...good(), iss: "https://evil.cloudflareaccess.com" })), env()), null);
  assert.equal(await accessOk(reqH(await token(good(), { kid: "unknown" })), env()), null);
});

test("аудитория проверяется, только если задана CF_ACCESS_AUD", async () => {
  const t = await token(good());
  assert.ok(await accessOk(reqH(t), { ...env(), CF_ACCESS_AUD: "aud-1, aud-9" }));
  assert.equal(await accessOk(reqH(t), { ...env(), CF_ACCESS_AUD: "aud-9" }), null);
});

test("мусор вместо токена не роняет проверку", async () => {
  for (const bad of ["abc", "a.b", "a.b.c", "!!!.???.***"]) {
    assert.equal(await accessOk(reqH(bad), env()), null, bad);
  }
});

test("копии помощника в шести гейтах совпадают с каноническим", () => {
  const canon = fs.readFileSync(path.join(ROOT, "access/cfaccess.js"), "utf8");
  const block = (s) => { const m = s.match(/\/\/ BEGIN accessOk[\s\S]*?\/\/ END accessOk/); return m ? m[0] : null; };
  const want = block(canon);
  assert.ok(want, "в access/cfaccess.js нет маркеров");
  for (const g of GATES) {
    const src = fs.readFileSync(path.join(ROOT, g), "utf8");
    assert.equal(block(src), want, `${g}: блок accessOk разошёлся с access/cfaccess.js`);
    assert.match(src, /const SITE = "(sourcing|gpu|ove|zip|gidromet|gok)";/, `${g}: нет константы SITE`);
    assert.doesNotMatch(src, /BASIC_AUTH|WWW-Authenticate/, `${g}: остался парольный вход`);
  }
});
