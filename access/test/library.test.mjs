// Синтетические данные; ни исходники CRM, ни производственные ключи не нужны.
import test from "node:test";
import assert from "node:assert/strict";
import worker from "../../public/_worker.js";
import { defaultAcl } from "../acl.js";

const ORIGIN = "https://portal.example.test";
const TEAM = "library-test.cloudflareaccess.com";
const OWNER = "owner@example.test";
const READER = "reader@example.test";
const GUEST = "guest@example.test";
const encoder = new TextEncoder();
const b64 = (v) => Buffer.from(v).toString("base64url");
const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
  publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...await crypto.subtle.exportKey("jwk", pair.publicKey), kid: "library-key", alg: "RS256" };
async function token(email) {
  const input = b64(JSON.stringify({ alg: "RS256", kid: jwk.kid })) + "." +
    b64(JSON.stringify({ email, iss: `https://${TEAM}`, exp: Math.floor(Date.now() / 1000) + 600 }));
  return input + "." + b64(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, encoder.encode(input)));
}

function envFor() {
  const box = new Map();
  const writes = [];
  const assets = [];
  const acl = defaultAcl();
  acl.defaultRole = "guest";
  acl.users[READER] = { role: "guest", sites: ["knowledge"], tabs: [] };
  box.set("acl:v1", JSON.stringify(acl));
  return { CF_ACCESS_TEAM: TEAM, __certs: { keys: [jwk] }, ADMIN_EMAILS: OWNER, assets, writes,
    ACL: { box, get: async (key, options) => {
      const raw = box.get(key) ?? null;
      return raw !== null && options?.type === "json" ? JSON.parse(raw) : raw;
    }, put: async (key, value) => { writes.push(key); box.set(key, value); },
      list: async ({ prefix = "", limit = 100, cursor = "0" }) => {
        const all = [...box.keys()].filter((key) => key.startsWith(prefix)).sort();
        const start = Number(cursor);
        return { keys: all.slice(start, start + limit).map((name) => ({ name })),
          list_complete: all.length <= start + limit, cursor: String(start + limit) };
      } },
    ASSETS: { fetch: async (request) => {
      assets.push(new URL(request.url).pathname);
      return new Response("<!doctype html><title>Library fixture</title><main>UI SHELL</main>", { headers: { "Content-Type": "text/html" } });
    } },
  };
}
const ctx = { waitUntil: (promise) => Promise.resolve(promise).catch(() => {}) };
async function call(env, path, email = READER, init = {}) {
  const headers = new Headers(init.headers);
  if (email) headers.set("Cf-Access-Jwt-Assertion", await token(email));
  return worker.fetch(new Request(ORIGIN + path, { ...init, headers }), env, ctx);
}
function sample(article = "synthetic-first", segment = "synthetic-compressors") {
  return { segments: [{ id: segment, name: "Учебное оборудование", note: "Вымышленный пример" }],
    articles: [{ id: article, segment_id: segment, title: "Учебная заметка", topic: "Сопоставление",
      body: "SYNTHETIC PRIVATE EVIDENCE <script>must remain text</script>",
      sources: { references: [{ title: "Учебный источник", locator: "Лист 1, B2", sha256: "a".repeat(64) }], limits: ["Только тест"] },
      confidence: "med", updated_at: "2026-01-01T00:00:00Z" }] };
}
async function publish(env, document = sample(), email = OWNER, headers = {}) {
  return call(env, "/admin/library", email, { method: "POST", headers: {
    Origin: ORIGIN, "Content-Type": "application/json", ...headers }, body: JSON.stringify(document) });
}

test("Access JWT is verified before library pages, API and publication", async () => {
  const env = envFor();
  await publish(env);
  for (const path of ["/library", "/library/", "/library.html", "/api/library", "/admin/library", "/admin/library/drafts"]) {
    const response = await call(env, path, null);
    assert.equal(response.status, 403, path);
    assert.doesNotMatch(await response.text(), /SYNTHETIC PRIVATE EVIDENCE|UI SHELL/);
  }
  const fake = await token(OWNER);
  const response = await call(env, "/api/library", null, { headers: { "Cf-Access-Jwt-Assertion": fake.slice(0, -6) + "AAAAAA" } });
  assert.equal(response.status, 403);
});

test("knowledge permission required even for authenticated direct links", async () => {
  const env = envFor();
  await publish(env);
  for (const path of ["/library", "/library/", "/library.html", "/api/library"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 403, path);
    assert.doesNotMatch(await response.text(), /SYNTHETIC PRIVATE EVIDENCE|UI SHELL/);
  }
  for (const path of ["/library", "/library/", "/library.html"]) {
    const response = await call(env, path);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Cache-Control"), "private, no-store, max-age=0");
    assert.match(response.headers.get("Content-Security-Policy"), /frame-ancestors 'none'/);
    assert.match(await response.text(), /UI SHELL/);
  }
  assert.deepEqual(env.assets, ["/library.html", "/library.html", "/library.html"]);
  const response = await call(env, "/api/library");
  assert.equal(response.headers.get("Content-Type"), "application/json; charset=utf-8");
  assert.equal(response.headers.get("X-Content-Type-Options"), "nosniff");
  const data = await response.json();
  assert.equal(data.admin, false);
  assert.equal(data.articles[0].body, sample().articles[0].body);
  assert.equal((await (await call(env, "/api/library", OWNER)).json()).admin, true);
});

test("alternate and unknown library paths cannot fall through to static assets", async () => {
  const env = envFor();
  for (const path of ["/%6cibrary.html", "/%256cibrary.html", "//library.html", "/LIBRARY.HTML", "/library/missing", "/api/library/", "/api/%6cibrary", "/admin/library/", "/library.html;ignored"]) {
    const response = await call(env, path, GUEST);
    assert.equal(response.status, 404, path);
  }
  assert.deepEqual(env.assets, []);
});

test("missing and unreadable storage are distinct from an empty library", async () => {
  const empty = await (await call(envFor(), "/api/library")).json();
  assert.deepEqual(empty.articles, []);
  assert.equal(empty.published_at, null);
  const missing = envFor();
  delete missing.ACL;
  assert.equal((await call(missing, "/api/library", OWNER)).status, 503);
  const broken = envFor();
  broken.ACL.get = async () => { throw new Error("offline"); };
  assert.equal((await call(broken, "/api/library")).status, 503);
  assert.equal((await publish(broken)).status, 503);
  const corrupt = envFor();
  corrupt.ACL.box.set("library:v1", "{broken");
  assert.equal((await call(corrupt, "/api/library")).status, 503);
  const corruptAcl = envFor();
  corruptAcl.ACL.box.set("acl:v1", "{}");
  assert.equal((await call(corruptAcl, "/api/library")).status, 503);
});

test("only owner may publish, with exact Origin and JSON media type", async () => {
  const env = envFor();
  assert.equal((await publish(env, sample(), READER)).status, 403);
  assert.equal((await publish(env, sample(), GUEST)).status, 403);
  assert.equal((await publish(env, sample(), OWNER, { Origin: "https://other.example.test" })).status, 403);
  assert.equal((await publish(env, sample(), OWNER, { Origin: "null" })).status, 403);
  assert.equal((await publish(env, sample(), OWNER, { "Content-Type": "text/plain" })).status, 415);
  assert.equal((await call(env, "/admin/library", OWNER)).status, 405);
  assert.equal((await call(env, "/api/library", READER, { method: "POST" })).status, 405);
  assert.equal(env.ACL.box.has("library:v1"), false);
});

test("publication merges stable IDs, preserves unrelated articles and historical copies", async () => {
  const env = envFor();
  const aclBefore = env.ACL.box.get("acl:v1");
  const first = await (await publish(env)).json();
  assert.equal(first.changed, true);
  const before = env.ACL.box.get("library:v1");
  const again = await (await publish(env)).json();
  assert.equal(again.changed, false);
  assert.equal(again.revision, first.revision);
  assert.equal(env.ACL.box.get("library:v1"), before);
  const second = await (await publish(env, sample("synthetic-second", "synthetic-other"))).json();
  assert.equal(second.articles, 2);
  assert.equal(env.ACL.box.get("library:history:" + first.revision), before);
  assert.ok(env.ACL.box.has("library:history:" + second.revision));
  const update = sample();
  update.segments = []; // referring to an existing segment is supported
  update.articles[0].body = "Updated synthetic evidence";
  assert.equal((await publish(env, update)).status, 200);
  const data = await (await call(env, "/api/library")).json();
  assert.equal(data.articles.length, 2);
  assert.equal(data.articles[0].body, "Updated synthetic evidence");
  assert.equal(data.articles[1].id, "synthetic-second");
  assert.equal(env.ACL.box.get("acl:v1"), aclBefore);
  assert.equal(env.writes.filter((key) => key === "acl:v1").length, 0);
});

test("invalid IDs, duplicate records and missing references cannot replace existing content", async () => {
  const env = envFor();
  await publish(env);
  const original = env.ACL.box.get("library:v1");
  const invalid = [];
  invalid.push({ ...sample(), articles: [...sample().articles, ...sample().articles] });
  invalid.push({ ...sample(), segments: [...sample().segments, ...sample().segments] });
  invalid.push({ segments: [], articles: sample("orphan", "missing").articles });
  invalid.push({ ...sample(), segments: [{ id: "../acl:v1", name: "Bad path" }] });
  invalid.push({ ...sample(), articles: [{ ...sample().articles[0], sources: "wrong shape" }] });
  invalid.push({ ...sample(), articles: [{ ...sample().articles[0], updated_at: "unknown" }] });
  for (const document of invalid) assert.equal((await publish(env, document)).status, 400);
  assert.equal(env.ACL.box.get("library:v1"), original);
});

test("streamed body byte limit works without a trustworthy Content-Length", async () => {
  const env = envFor();
  const stream = new ReadableStream({ start(controller) {
    controller.enqueue(encoder.encode(" ".repeat(2200000)));
    controller.enqueue(encoder.encode(" ".repeat(2200000)));
    controller.close();
  } });
  const response = await call(env, "/admin/library", OWNER, { method: "POST", duplex: "half", body: stream,
    headers: { Origin: ORIGIN, "Content-Type": "application/json", "Content-Length": "2" } });
  assert.equal(response.status, 413);
  assert.equal(env.ACL.box.has("library:v1"), false);
});

test("limits apply to the merged library, and a failed history write preserves current version", async () => {
  const env = envFor();
  const many = sample();
  many.articles = Array.from({ length: 5000 }, (_, index) => ({ ...many.articles[0], id: `synthetic-${index}` }));
  assert.equal((await publish(env, many)).status, 200);
  const before = env.ACL.box.get("library:v1");
  assert.equal((await publish(env, sample("one-too-many"))).status, 400);
  assert.equal(env.ACL.box.get("library:v1"), before);
  const failed = envFor();
  await publish(failed);
  const saved = failed.ACL.box.get("library:v1");
  const put = failed.ACL.put;
  failed.ACL.put = async (key, value) => {
    if (key.startsWith("library:history:")) throw new Error("history unavailable");
    return put(key, value);
  };
  assert.equal((await publish(failed, sample("synthetic-second"))).status, 503);
  assert.equal(failed.ACL.box.get("library:v1"), saved);
});

test("portal presents the new library alongside equipment libraries only when permitted", async () => {
  const env = envFor();
  const permitted = await (await call(env, "/", READER)).text();
  assert.match(permitted, /Библиотека оборудования и знаний/);
  assert.match(permitted, /href="\/library"/);
  assert.doesNotMatch(await (await call(env, "/", GUEST)).text(), /href="\/library/);
});

function draftArticle(kind = "knowledge") {
  return { ...sample().articles[0], id: "draft:" + crypto.randomUUID(), sources: {
    kind, typedfields: { component: "Synthetic component", supplier_role: "manufacturer", currency: "EUR" },
    references: [{ title: "Synthetic source", url: "https://example.test/source" }],
  } };
}
async function queue(env, article, email = OWNER, headers = {}) {
  return call(env, "/admin/library/drafts", email, { method: "POST", headers: {
    Origin: ORIGIN, "Content-Type": "application/json", ...headers }, body: JSON.stringify(article) });
}

test("owner drafts remain pending without a dispatch token and retries never duplicate records", async () => {
  const env = envFor();
  await publish(env);
  const article = draftArticle();
  const first = await (await queue(env, article)).json();
  assert.deepEqual(first, { ok: true, id: article.id, status: "pending", queued: true, sync_requested: false });
  const key = "library:draft:" + article.id;
  const original = env.ACL.box.get(key);
  assert.deepEqual(JSON.parse(original).article, article);
  assert.equal((await queue(env, { article })).status, 200, "wrapped article is also accepted");
  assert.equal(env.ACL.box.get(key), original);
  assert.equal(env.writes.filter((item) => item === key).length, 1);
  assert.equal((await queue(env, { ...article, body: "different content" })).status, 409);
  assert.equal(env.ACL.box.get(key), original);
  assert.equal((await (await call(env, "/api/library")).json()).articles.length, 1, "unpublished drafts are not shown as verified knowledge");
  const saved = JSON.parse(original);
  saved.status = "published";
  saved.published_at = "2026-01-02T00:00:00Z";
  env.ACL.box.set(key, JSON.stringify(saved));
  assert.equal((await (await queue(env, article)).json()).queued, false);
});

test("draft validation and owner access apply to both creation and status listing", async () => {
  const env = envFor();
  await publish(env);
  const article = draftArticle();
  assert.equal((await queue(env, article, READER)).status, 403);
  assert.equal((await call(env, "/admin/library/drafts", READER)).status, 403);
  assert.equal((await call(env, "/admin/library/drafts", GUEST)).status, 403);
  assert.equal((await queue(env, article, OWNER, { Origin: "https://other.example.test" })).status, 403);
  assert.equal((await queue(env, article, OWNER, { "Content-Type": "text/plain" })).status, 415);
  assert.equal((await queue(env, { ...article, id: "acl:v1" })).status, 400);
  assert.equal((await queue(env, { ...article, segment_id: "not-in-library" })).status, 400);
  assert.equal((await queue(env, draftArticle("unsupported"))).status, 400);
  assert.equal((await queue(env, { ...article, sources: { kind: "supplier", typedfields: "bad" } })).status, 400);
  assert.equal([...env.ACL.box.keys()].some((key) => key.startsWith("library:draft:")), false);
});

test("draft status listing is bounded, paginated and excludes document bodies", async () => {
  const env = envFor();
  await publish(env);
  for (const kind of ["knowledge", "supplier", "price", "component"]) assert.equal((await queue(env, draftArticle(kind))).status, 200);
  const page = await (await call(env, "/admin/library/drafts?limit=2", OWNER)).json();
  assert.equal(page.drafts.length, 2);
  assert.equal(page.list_complete, false);
  assert.ok(page.cursor);
  assert.equal(page.drafts[0].body, undefined);
  assert.equal(page.drafts[0].sources, undefined);
  const second = await (await call(env, "/admin/library/drafts?limit=2&cursor=" + page.cursor, OWNER)).json();
  assert.equal(second.drafts.length, 2);
  assert.equal(second.list_complete, true);
  assert.equal(new Set([...page.drafts, ...second.drafts].map((row) => row.id)).size, 4);
});

test("dispatch sends only a generic event, and failed dispatch never discards the draft", async () => {
  const env = envFor();
  await publish(env);
  env.GH_DISPATCH_TOKEN = "synthetic-test-token";
  const originalFetch = globalThis.fetch;
  const requests = [];
  try {
    globalThis.fetch = async (url, init) => { requests.push({ url, init }); return new Response(null, { status: 204 }); };
    const article = draftArticle();
    assert.equal((await (await queue(env, article)).json()).sync_requested, true);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "https://api.github.com/repos/skharkov1246/kvant-sourcing-dashboard/dispatches");
    assert.deepEqual(JSON.parse(requests[0].init.body), { event_type: "library-update" });
    assert.ok(!requests[0].init.body.includes(article.id));
    globalThis.fetch = async () => { throw new Error("unavailable"); };
    const another = draftArticle();
    const response = await (await queue(env, another)).json();
    assert.equal(response.sync_requested, false);
    assert.equal(response.status, "pending");
    assert.ok(env.ACL.box.has("library:draft:" + another.id));
  } finally { globalThis.fetch = originalFetch; }
});

test("existing snapshots are backed up byte-for-byte, including external JSON formatting", async () => {
  const env = envFor();
  await publish(env);
  const snapshot = JSON.parse(env.ACL.box.get("library:v1"));
  const formatted = JSON.stringify(snapshot, null, 2);
  env.ACL.box.set("library:v1", formatted);
  env.ACL.box.set("library:history:" + snapshot.revision, formatted);
  assert.equal((await publish(env, sample("another-record"))).status, 200);
  assert.equal(env.ACL.box.get("library:history:" + snapshot.revision), formatted);
});
