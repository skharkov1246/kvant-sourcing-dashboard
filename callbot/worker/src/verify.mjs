// Проверка подписей вебхуков звонилки. Всё — на WebCrypto (есть и в Workers, и в Node 22).
// Источники (разбор API 31.10.2026):
//  OpenAI — Standard Webhooks: webhook-id / webhook-timestamp / webhook-signature «v1,<base64>»,
//           HMAC-SHA256 над «id.timestamp.body», ключ — base64 после «whsec_»;
//  ElevenLabs — заголовок ElevenLabs-Signature «t=<unix>,v0=<hex>», HMAC-SHA256 над «t.body»,
//           ключ — секрет как текст UTF-8 (исходник официального SDK elevenlabs-js);
//  Telnyx — Ed25519: telnyx-signature-ed25519 (base64) над «timestamp|body», открытый ключ
//           аккаунта (base64), допуск 300 с.

const enc = new TextEncoder();

export function b64ToBytes(s) {
  const bin = atob(String(s || "").trim());
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64(u) {
  let s = "";
  for (const b of u) s += String.fromCharCode(b);
  return btoa(s);
}

function hex(u) {
  return [...u].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// сравнение без раннего выхода: время не выдаёт, сколько символов совпало
function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function hmacSha256(keyBytes, message) {
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
}

function fresh(ts, now, tolerance) {
  const t = Number(ts);
  return Number.isFinite(t) && Math.abs(now - t) <= tolerance;
}

export async function verifyOpenAI(secret, headers, body, now = Date.now() / 1000, tolerance = 300) {
  const id = headers.get("webhook-id");
  const ts = headers.get("webhook-timestamp");
  const sig = headers.get("webhook-signature");
  if (!secret || !id || !ts || !sig || !fresh(ts, now, tolerance)) return false;
  let key;
  try {
    key = b64ToBytes(secret.startsWith("whsec_") ? secret.slice(6) : secret);
  } catch {
    return false;
  }
  const expected = bytesToB64(await hmacSha256(key, `${id}.${ts}.${body}`));
  return sig.split(" ").some((part) => {
    const [version, value] = part.split(",");
    return version === "v1" && safeEqual(value, expected);
  });
}

export async function verifyEleven(secret, header, body, now = Date.now() / 1000, tolerance = 1800) {
  if (!secret || !header) return false;
  const parts = Object.fromEntries(String(header).split(",").map((p) => p.trim().split("=")).filter((p) => p.length === 2));
  if (!parts.t || !parts.v0 || !fresh(parts.t, now, tolerance)) return false;
  const expected = hex(await hmacSha256(enc.encode(secret), `${parts.t}.${body}`));
  return safeEqual(parts.v0, expected);
}

export async function verifyTelnyx(publicKeyB64, signatureB64, ts, body, now = Date.now() / 1000, tolerance = 300) {
  if (!publicKeyB64 || !signatureB64 || !ts || !fresh(ts, now, tolerance)) return false;
  try {
    const key = await crypto.subtle.importKey("raw", b64ToBytes(publicKeyB64), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64ToBytes(signatureB64), enc.encode(`${ts}|${body}`));
  } catch {
    return false;
  }
}
