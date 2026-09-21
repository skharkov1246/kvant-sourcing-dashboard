// Private library reader. Call only after verified Access JWT + knowledge ACL.
// No mutations, list-all calls, background scans, or client-supplied KV keys.
const CURRENT = "library:v2:current";
const PREFIX = "library:v2:blob:";
const ID = /^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$/;
const HASH = /^[a-f0-9]{64}$/;
const KINDS = ["knowledge", "supplier", "price", "component"];
const VALUE_BYTES = 4 * 1024 * 1024;
const RESPONSE_BYTES = 2 * 1024 * 1024;
const READ_LIMIT = 18;
const BYTE_LIMIT = 12 * 1024 * 1024;
const encoder = new TextEncoder();

class Failure extends Error {
  constructor(code, status = 503) { super(code); this.status = status; }
}
class Budget extends Error {}
function requireValue(condition, code = "library_v2_corrupt", status = 503) {
  if (!condition) throw new Failure(code, status);
}
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function integer(value, min, max) { return Number.isSafeInteger(value) && value >= min && value <= max; }
async function sha(raw) {
  const result = await crypto.subtle.digest("SHA-256", encoder.encode(raw));
  return [...new Uint8Array(result)].map((v) => v.toString(16).padStart(2, "0")).join("");
}
function json(raw) {
  requireValue(typeof raw === "string" && encoder.encode(raw).length <= VALUE_BYTES);
  try { return JSON.parse(raw); } catch { throw new Failure("library_v2_corrupt"); }
}
function reference(ref) {
  requireValue(object(ref) && HASH.test(ref.sha256) && integer(ref.bytes, 1, VALUE_BYTES));
  return ref;
}
function publicResponse(value, status = 200, extra = {}) {
  const raw = JSON.stringify(value);
  requireValue(encoder.encode(raw).length <= RESPONSE_BYTES, "library_v2_response_limit");
  return new Response(raw, { status, headers: { "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store, max-age=0", "X-Robots-Tag": "noindex, nofollow, noarchive",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY",
    "Vary": "Cookie, Cf-Access-Jwt-Assertion", ...extra } });
}

class Reader {
  constructor(kv) { this.kv = kv; this.reads = 0; this.bytes = 0; this.cache = new Map(); }
  async raw(key, expectedBytes = VALUE_BYTES) {
    if (this.cache.has(key)) return this.cache.get(key);
    if (this.reads >= READ_LIMIT || this.bytes + expectedBytes > BYTE_LIMIT) throw new Budget();
    this.reads++;
    const raw = await this.kv.get(key);
    if (raw !== null) {
      requireValue(typeof raw === "string");
      this.bytes += encoder.encode(raw).length;
      requireValue(this.bytes <= BYTE_LIMIT);
    }
    this.cache.set(key, raw);
    return raw;
  }
  async blob(ref) {
    reference(ref);
    const raw = await this.raw(PREFIX + ref.sha256, ref.bytes);
    requireValue(raw !== null, "library_v2_shard_unavailable");
    requireValue(encoder.encode(raw).length === ref.bytes && await sha(raw) === ref.sha256,
      "library_v2_shard_integrity");
    return json(raw);
  }
}

function validateManifest(manifest, pointer) {
  requireValue(object(manifest) && manifest.version === 2 && manifest.revision === pointer.revision &&
    typeof manifest.published_at === "string" && manifest.published_at === pointer.published_at &&
    integer(manifest.article_count, 0, 250000) && Array.isArray(manifest.segments) && manifest.segments.length <= 100);
  const seen = new Set();
  const confidence = Object.create(null);
  let count = 0;
  for (const segment of manifest.segments) {
    requireValue(object(segment) && ID.test(segment.id) && !seen.has(segment.id) &&
      typeof segment.name === "string" && object(segment.counts_by_kind) && object(segment.indexes));
    seen.add(segment.id);
    let total = 0;
    for (const kind of KINDS) {
      const n = segment.counts_by_kind[kind];
      requireValue(integer(n, 0, 250000) && object(segment.indexes[kind]));
      for (const category of ["catalog", "search"]) {
        const ref = segment.indexes[kind][category];
        if (n === 0) requireValue(ref === null);
        else { reference(ref); requireValue(ref.count === n); }
      }
      total += n;
    }
    requireValue(segment.article_count === total);
    requireValue(object(segment.counts_by_confidence) && Object.values(segment.counts_by_confidence)
      .every((n) => integer(n, 0, total)) && Object.values(segment.counts_by_confidence).reduce((a, b) => a + b, 0) === total);
    for (const [key, n] of Object.entries(segment.counts_by_confidence)) confidence[key] = (confidence[key] || 0) + n;
    count += total;
  }
  requireValue(count === manifest.article_count);
  requireValue(object(manifest.counts_by_confidence) && Object.keys(confidence).length === Object.keys(manifest.counts_by_confidence).length &&
    Object.entries(confidence).every(([key, n]) => manifest.counts_by_confidence[key] === n));
  if (count) { reference(manifest.directory); requireValue(manifest.directory.count === count); }
  else requireValue(manifest.directory === null);
}

async function load(reader, revision = null) {
  if (revision !== null) requireValue(ID.test(revision), "invalid_revision", 400);
  const raw = await reader.raw(revision === null ? CURRENT : "library:v2:revision:" + revision);
  if (raw === null) throw new Failure(revision === null ? "library_v2_unavailable" : "library_v2_revision_unavailable",
    revision === null ? 404 : 409);
  const pointer = json(raw);
  requireValue(object(pointer) && pointer.version === 2 && ID.test(pointer.revision) &&
    (revision === null || pointer.revision === revision));
  const manifest = await reader.blob(pointer.manifest);
  validateManifest(manifest, pointer);
  return manifest;
}

async function node(reader, ref, category) {
  const value = await reader.blob(ref);
  requireValue(object(value) && value.category === category && ID.test(ref.first) && ID.test(ref.last) &&
    ref.first <= ref.last && integer(ref.count, 1, 250000));
  if (value.type === "branch") {
    requireValue(Array.isArray(value.children) && value.children.length > 0 && value.children.length <= 64);
    let n = 0, last = null;
    for (const child of value.children) {
      reference(child);
      requireValue(integer(child.count, 1, ref.count) && ID.test(child.first) && ID.test(child.last) &&
        child.first <= child.last && (last === null || child.first > last));
      n += child.count; last = child.last;
    }
    requireValue(n === ref.count && value.children[0].first === ref.first && last === ref.last);
  } else {
    requireValue(value.type === "leaf" && Array.isArray(value.items) && value.items.length === ref.count &&
      value.items.length <= 50);
    let last = null;
    for (const item of value.items) {
      requireValue(object(item) && ID.test(item.id) && (last === null || item.id > last));
      last = item.id;
    }
    requireValue(value.items[0].id === ref.first && last === ref.last);
  }
  return value;
}

async function at(reader, ref, category, offset, depth = 0) {
  requireValue(depth < 8 && integer(offset, 0, ref.count - 1));
  const value = await node(reader, ref, category);
  if (value.type === "leaf") return { items: value.items, start: offset };
  for (const child of value.children) {
    if (offset < child.count) return at(reader, child, category, offset, depth + 1);
    offset -= child.count;
  }
  throw new Failure("library_v2_corrupt");
}

async function find(reader, ref, id, depth = 0) {
  if (ref === null) return null;
  requireValue(depth < 8);
  if (id < ref.first || id > ref.last) return null;
  const value = await node(reader, ref, "directory");
  if (value.type === "leaf") return value.items.find((item) => item.id === id) || null;
  const child = value.children.find((item) => id >= item.first && id <= item.last);
  return child ? find(reader, child, id, depth + 1) : null;
}

function cursorEncode(value) { return btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
function cursorDecode(value) {
  requireValue(typeof value === "string" && value.length <= 2048 && /^[A-Za-z0-9_-]+$/.test(value), "invalid_cursor", 400);
  try {
    const result = JSON.parse(atob(value.replace(/-/g, "+").replace(/_/g, "/")));
    requireValue(object(result) && cursorEncode(result) === value, "invalid_cursor", 400);
    return result;
  } catch { throw new Failure("invalid_cursor", 400); }
}

function selectedGroups(manifest, segmentId, kind, category) {
  const segments = manifest.segments.filter((s) => !segmentId || s.id === segmentId);
  requireValue(!segmentId || segments.length === 1, "unknown_segment", 400);
  return segments.flatMap((segment) => KINDS.filter((k) => !kind || k === kind).map((k) => ({
    segment: segment.id, kind: k, count: segment.counts_by_kind[k], ref: segment.indexes[k][category] }))).filter((g) => g.count);
}

async function listing(reader, manifest, url) {
  const allowed = new Set(["revision", "segment_id", "kind", "q", "limit", "cursor"]);
  requireValue([...url.searchParams.keys()].every((k) => allowed.has(k) && url.searchParams.getAll(k).length === 1), "invalid_query", 400);
  const segmentId = url.searchParams.get("segment_id") || "";
  const kind = url.searchParams.get("kind") || "";
  const q = (url.searchParams.get("q") || "").trim().toLowerCase();
  const limitRaw = url.searchParams.get("limit") || "25";
  requireValue((!segmentId || ID.test(segmentId)) && (!kind || KINDS.includes(kind)) && q.length <= 200 &&
    /^[1-9][0-9]?$/.test(limitRaw) && Number(limitRaw) <= 50, "invalid_query", 400);
  const limit = Number(limitRaw);
  const fingerprint = await sha(JSON.stringify([manifest.revision, segmentId, kind, q, limit]));
  const category = q ? "search" : "catalog";
  const groups = selectedGroups(manifest, segmentId, kind, category);
  const scopeTotal = groups.reduce((n, g) => n + g.count, 0);
  let group = 0, offset = 0;
  if (url.searchParams.has("cursor")) {
    const c = cursorDecode(url.searchParams.get("cursor"));
    requireValue(c.v === 2 && c.query === fingerprint && c.revision === manifest.revision &&
      integer(c.group, 0, groups.length - 1) && integer(c.offset, 0, groups[c.group]?.count - 1), "invalid_cursor", 400);
    group = c.group; offset = c.offset;
  }
  const items = [];
  let scanned = 0;
  while (group < groups.length && items.length < limit) {
    const current = groups[group];
    let leaf;
    try { leaf = await at(reader, current.ref, category, offset); }
    catch (error) { if (error instanceof Budget && scanned > 0) break; throw error; }
    for (let index = leaf.start; index < leaf.items.length && items.length < limit; index++) {
      const entry = leaf.items[index];
      const summary = q ? entry.summary : entry;
      requireValue(object(summary) && summary.id === entry.id && summary.segment_id === current.segment &&
        object(summary.sources) && summary.sources.kind === current.kind &&
        typeof summary.title === "string" && typeof summary.excerpt === "string" &&
        encoder.encode(JSON.stringify(summary)).length <= 32 * 1024);
      requireValue(!q || typeof entry.text === "string");
      if (!q || entry.text.includes(q)) items.push(summary);
      scanned++; offset++;
    }
    if (offset === current.count) { group++; offset = 0; }
  }
  const complete = group === groups.length;
  const next = complete ? null : cursorEncode({ v: 2, revision: manifest.revision, query: fingerprint, group, offset });
  return { version: 2, revision: manifest.revision, items, next_cursor: next, complete, scanned,
    matched_in_batch: items.length, total: q ? null : scopeTotal, scope_total: scopeTotal,
    search_scope: q ? "full_title_topic_body_sources_substring" : null, order: "segment_kind_id" };
}

export async function libraryV2(request, env, admin = false) {
  try {
    if (request.method !== "GET") return publicResponse({ error: "method_not_allowed" }, 405, { Allow: "GET" });
    const kv = env && (env.ACL || env.VISITS);
    requireValue(kv, "library_v2_unavailable", 503);
    const reader = new Reader(kv);
    const url = new URL(request.url);
    const manifest = await load(reader, url.searchParams.get("revision"));
    if (url.pathname === "/api/library/v2") {
      requireValue([...url.searchParams.keys()].every((k) => k === "revision") && url.searchParams.getAll("revision").length <= 1, "invalid_query", 400);
      return publicResponse({ version: 2, revision: manifest.revision, published_at: manifest.published_at,
        article_count: manifest.article_count, counts_by_confidence: manifest.counts_by_confidence,
        segments: manifest.segments.map(({ indexes, ...s }) => s), admin,
        capabilities: { pagination: true, search: "progressive_full_text_substring", revision_pinning: true,
          max_page_size: 50, order: "segment_kind_id" } });
    }
    if (url.pathname === "/api/library/v2/articles") return publicResponse(await listing(reader, manifest, url));
    if (url.pathname === "/api/library/v2/article") {
      requireValue([...url.searchParams.keys()].every((k) => ["revision", "id"].includes(k) && url.searchParams.getAll(k).length === 1), "invalid_query", 400);
      const id = url.searchParams.get("id");
      requireValue(ID.test(id || ""), "invalid_article_id", 400);
      const ref = await find(reader, manifest.directory, id);
      if (!ref) return publicResponse({ error: "article_not_found" }, 404);
      const value = await reader.blob(ref.block);
      requireValue(value.type === "articles" && Array.isArray(value.items) && value.items.length <= 50 &&
        integer(ref.index, 0, value.items.length - 1));
      const article = value.items[ref.index];
      requireValue(object(article) && article.id === id && manifest.segments.some((s) => s.id === article.segment_id));
      return publicResponse({ revision: manifest.revision, article });
    }
    return publicResponse({ error: "not_found" }, 404);
  } catch (error) {
    if (error instanceof Failure) return publicResponse({ error: error.message }, error.status);
    return publicResponse({ error: error instanceof Budget ? "library_v2_budget_exceeded" : "library_v2_unavailable" }, 503);
  }
}

export async function libraryV2Segments(env) {
  const kv = env && (env.ACL || env.VISITS);
  requireValue(kv, "library_v2_unavailable", 503);
  const reader = new Reader(kv);
  // null is the only permitted fallback signal; corruption and quota failures
  // must not silently permit validation against a stale v1 schema.
  if (await reader.raw(CURRENT) === null) return null;
  const manifest = await load(reader);
  return { revision: manifest.revision, segments: manifest.segments };
}
