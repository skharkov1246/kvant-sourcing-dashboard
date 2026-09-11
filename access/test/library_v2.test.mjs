import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { libraryV2, libraryV2Segments } from "../../public/library_v2.js";

const python = process.env.LIBRARY_TEST_PYTHON || "python3";
const fixture = fileURLToPath(new URL("../../tests/test_publish_library_v2.py", import.meta.url));
const raw = execFileSync(python, [fixture, "6001"], { maxBuffer: 64 * 1024 * 1024,
  env: { PATH: process.env.PATH, PYTHONDONTWRITEBYTECODE: "1", PYTHONPATH: process.env.PYTHONPATH || "" } });
const initial = JSON.parse(raw);
const revision = JSON.parse(initial["library:v2:current"]).revision;
const base = "https://portal.example.test";
function env(values = initial) {
  const calls = [];
  const data = new Map(Object.entries(values));
  return { calls, data, ACL: { get: async (key) => { calls.push(key); return data.get(key) ?? null; } } };
}
async function call(e, path, init) {
  const start = e.calls.length;
  const response = await libraryV2(new Request(base + path, init), e);
  assert.ok(e.calls.length - start <= 18, "bounded library KV reads");
  assert.ok(Number(response.headers.get("content-length") || 0) <= 2 * 1024 * 1024);
  const text = await response.text();
  assert.ok(Buffer.byteLength(text) <= 2 * 1024 * 1024);
  return { status: response.status, value: JSON.parse(text), headers: response.headers };
}
function url(path, params) { return path + "?" + new URLSearchParams(params); }

test("cross-runtime published 6001 records: small manifest no all-body payload", async () => {
  const e = env();
  const { status, value, headers } = await call(e, "/api/library/v2");
  assert.equal(status, 200);
  assert.equal(value.article_count, 6001);
  assert.equal(value.articles, undefined);
  assert.equal(value.segments[0].indexes, undefined);
  assert.match(headers.get("cache-control"), /private.*no-store/);
  assert.match(headers.get("x-robots-tag"), /noindex/);
  assert.equal(e.calls.length, 2);
});

test("real pagination all records exactly once, full record only on deep link", async () => {
  const e = env();
  const ids = new Set();
  let cursor = null, complete = false, pages = 0;
  while (!complete) {
    const params = { revision, limit: "50" };
    if (cursor) params.cursor = cursor;
    const r = await call(e, url("/api/library/v2/articles", params));
    assert.equal(r.status, 200);
    assert.equal(r.value.total, 6001);
    assert.ok(r.value.items.length <= 50);
    for (const item of r.value.items) {
      assert.equal(item.body, undefined);
      assert.equal(typeof item.excerpt, "string");
      assert.ok(item.sources.typedfields?.part_number);
      assert.ok(!ids.has(item.id)); ids.add(item.id);
    }
    cursor = r.value.next_cursor; complete = r.value.complete; pages++;
    assert.ok(pages < 200);
  }
  assert.equal(ids.size, 6001);
  assert.ok(pages > 120);
  const detail = await call(e, url("/api/library/v2/article", { revision, id: "synthetic:006000" }));
  assert.equal(detail.status, 200);
  assert.equal(detail.value.article.body, "Synthetic full evidence Ω supplier μ\n6000");
  assert.deepEqual(detail.value.article.extra_original, { keep: true });
});

test("progressive full-source search resumes beyond empty partial pages and never invents total", async () => {
  const e = env();
  let cursor = null, complete = false, scanned = 0, pages = 0;
  const items = [];
  while (!complete) {
    const params = { revision, q: "PN-5999/A.1", limit: "25" };
    if (cursor) params.cursor = cursor;
    const r = await call(e, url("/api/library/v2/articles", params));
    assert.equal(r.status, 200);
    assert.equal(r.value.total, null);
    assert.equal(r.value.matched_in_batch, r.value.items.length);
    scanned += r.value.scanned; items.push(...r.value.items);
    complete = r.value.complete; cursor = r.value.next_cursor; pages++;
    if (!complete) assert.ok(cursor && r.value.scanned > 0);
    assert.ok(pages < 100);
  }
  assert.ok(pages > 1);
  assert.equal(scanned, 6001);
  assert.deepEqual(items.map((r) => r.id), ["synthetic:005999"]);
});

test("server filters exact kind/segment and Unicode full body survives", async () => {
  const e = env();
  const r = await call(e, url("/api/library/v2/articles", { revision, segment_id: "s1", kind: "supplier", q: "EVIDENCE Ω SUPPLIER Μ", limit: "25" }));
  assert.equal(r.status, 200);
  assert.equal(r.value.items.length, 25);
  assert.ok(r.value.items.every((v) => v.sources.kind === "supplier" && v.segment_id === "s1"));
  const empty = await call(e, url("/api/library/v2/articles", { revision, segment_id: "s2" }));
  assert.equal(empty.value.complete, true);
  assert.equal(empty.value.scope_total, 0);
});

test("cursor is pinned to revision, query, scope and page size", async () => {
  const e = env();
  const first = await call(e, url("/api/library/v2/articles", { revision, limit: "25" }));
  const cursor = first.value.next_cursor;
  for (const params of [{ q: "different" }, { kind: "price" }, { limit: "50" }, { segment_id: "s2" }]) {
    const bad = await call(e, url("/api/library/v2/articles", { revision, cursor, ...params }));
    assert.equal(bad.status, 400);
    assert.equal(bad.value.error, "invalid_cursor");
  }
  for (const c of ["!!!!", "a".repeat(2049), Buffer.from(JSON.stringify({ v: 2, group: -1 })).toString("base64url")]) {
    assert.equal((await call(e, url("/api/library/v2/articles", { revision, cursor: c }))).status, 400);
  }
});

test("new current never mixes a pinned reader's old manifest", async () => {
  const e = env();
  e.data.set("library:v2:current", "{\"broken\":true}");
  const pinned = await call(e, url("/api/library/v2/article", { revision, id: "synthetic:000194" }));
  assert.equal(pinned.status, 200);
  assert.equal((await call(e, "/api/library/v2")).status, 503);
});

test("only absent v2 current returns fallback404; corrupt or missing shards fail closed", async () => {
  const absent = env({});
  assert.deepEqual((await call(absent, "/api/library/v2")).value, { error: "library_v2_unavailable" });
  assert.equal((await call(absent, "/api/library/v2")).status, 404);
  assert.equal(await libraryV2Segments(absent), null);
  const e = env();
  const pointer = JSON.parse(e.data.get("library:v2:current"));
  e.data.delete("library:v2:blob:" + pointer.manifest.sha256);
  const missing = await call(e, "/api/library/v2");
  assert.equal(missing.status, 503);
  assert.equal(missing.value.error, "library_v2_shard_unavailable");
  await assert.rejects(libraryV2Segments(e));
  e.data.set("library:v2:blob:" + pointer.manifest.sha256, "{}");
  assert.equal((await call(e, "/api/library/v2")).value.error, "library_v2_shard_integrity");
});

test("invalid queries/ids/methods never list unrelated KV or mutate", async () => {
  const e = env();
  for (const path of ["/api/library/v2/articles?limit=0", "/api/library/v2/articles?limit=51",
    "/api/library/v2/articles?kind=unknown", "/api/library/v2/articles?segment_id=no-such",
    "/api/library/v2/articles?cursor=../acl", "/api/library/v2/articles?limit=2&limit=3",
    "/api/library/v2/article?id=../acl", "/api/library/v2/article?key=acl:v1"]) {
    assert.equal((await call(e, path)).status, 400, path);
  }
  assert.equal((await call(e, "/api/library/v2", { method: "POST" })).status, 405);
  assert.equal((await call(e, "/api/library/v2/article?id=absent")).status, 404);
  assert.ok(e.calls.every((key) => key.startsWith("library:v2:")));
});
