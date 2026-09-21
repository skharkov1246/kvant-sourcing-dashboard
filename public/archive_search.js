// Private archive: called only after Access JWT verification and strict admin ACL.
// Fixed read RPCs; no generic proxy, anonymous fallback, retries, or query logs.
const ORIGIN = "https://vpjliavuuxjcvtxbthlp.supabase.co";
const MAX_RESPONSE_BYTES = 1024 * 1024;
const UNIT_RESPONSE_BYTES = 128 * 1024;
const STATUS_RESPONSE_BYTES = 256 * 1024;
const SOURCE_KINDS = ["crm", "activity", "timeline", "mail", "chat", "task", "attachment", "other"];
const TIMEOUT_MS = 10000;
const encoder = new TextEncoder();

export function archiveRoute(path) {
  if (["/library/archive", "/library/archive/", "/archive.html"].includes(path)) return "page";
  if (path === "/api/library/archive/search") return "search";
  if (path === "/api/library/archive/status") return "status";
  if (path === "/api/library/archive/unit") return "unit";
  let decoded = path;
  for (let i = 0; i < 8 && decoded.includes("%"); i++) {
    try { const next = decodeURIComponent(decoded); if (next === decoded) break; decoded = next; }
    catch { break; }
  }
  decoded = decoded.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
  return /^\/(?:library\/archive(?:[/.;]|$)|api\/library\/archive(?:[/.;]|$)|archive(?:[/.;_]|$))/i.test(decoded) ? "invalid" : null;
}

export function archiveHeaders(initial) {
  const headers = new Headers(initial);
  headers.set("Cache-Control", "private, no-store, max-age=0");
  headers.set("Pragma", "no-cache");
  headers.set("X-Robots-Tag", "noindex, nofollow, noarchive");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "no-referrer");
  headers.set("X-Frame-Options", "DENY");
  headers.set("Vary", "Cookie, Cf-Access-Jwt-Assertion");
  return headers;
}

export function archiveJson(value, status = 200, extra = {}) {
  const headers = archiveHeaders(extra);
  headers.set("Content-Type", "application/json; charset=utf-8");
  return new Response(JSON.stringify(value), { status, headers });
}

class ArchiveFailure extends Error {
  constructor(code, status = 503) { super(code); this.status = status; }
}
function requireValue(value, code = "archive_response_invalid", status = 503) {
  if (!value) throw new ArchiveFailure(code, status);
}
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function string(value, max, nullable = false) {
  requireValue((nullable && value === null) || (typeof value === "string" && [...value].length <= max));
  return value;
}
function byteString(value, max, nullable = false) {
  requireValue((nullable && value === null) || (typeof value === "string" && encoder.encode(value).length <= max));
  return value;
}

export function archiveQuery(url, route) {
  const allowed = route === "search" ? ["q", "limit", "source_kind"] : route === "unit" ? ["extraction_id", "unit_key"] : [];
  requireValue([...url.searchParams.keys()].every((key) => allowed.includes(key) && url.searchParams.getAll(key).length === 1), "invalid_archive_query", 400);
  if (route === "status") return {};
  if (route === "unit") {
    const extractionId = url.searchParams.get("extraction_id"), unitKey = url.searchParams.get("unit_key");
    requireValue(typeof extractionId === "string" && /^[1-9][0-9]{0,18}$/.test(extractionId) && BigInt(extractionId) <= 9223372036854775807n &&
      typeof unitKey === "string" && unitKey.length >= 1 && encoder.encode(unitKey).length <= 1024 &&
      !/[\u0000-\u001f\u007f]/.test(unitKey), "invalid_archive_query", 400);
    return { extraction_id: extractionId, unit_key: unitKey };
  }
  const q = (url.searchParams.get("q") || "").trim();
  const limitRaw = url.searchParams.get("limit") || "20";
  const sourceKind = url.searchParams.get("source_kind") || null;
  requireValue([...q].length >= 2 && [...q].length <= 200 && encoder.encode(q).length <= 800 && !/[\u0000-\u001f\u007f]/.test(q), "invalid_archive_query", 400);
  requireValue(/^[1-9][0-9]?$/.test(limitRaw) && Number(limitRaw) <= 50, "invalid_archive_query", 400);
  requireValue(sourceKind === null || SOURCE_KINDS.includes(sourceKind), "invalid_archive_query", 400);
  return { search_query: q, result_limit: Number(limitRaw), source_kind: sourceKind };
}

async function readBounded(response, maxBytes) {
  const declared = response.headers.get("Content-Length");
  try {
    requireValue(declared === null || (/^\d+$/.test(declared) && Number(declared) <= maxBytes), "archive_response_too_large");
    requireValue(response.body && /^application\/json(?:;|$)/i.test(response.headers.get("Content-Type") || ""));
  } catch (error) { try { await response.body?.cancel(); } catch {} throw error; }
  const reader = response.body.getReader();
  const chunks = []; let bytes = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      requireValue(bytes <= maxBytes, "archive_response_too_large");
      chunks.push(value);
    }
  } catch (error) { try { await reader.cancel(); } catch {} throw error; }
  finally { reader.releaseLock(); }
  const combined = new Uint8Array(bytes); let offset = 0;
  for (const chunk of chunks) { combined.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(combined)); }
  catch { throw new ArchiveFailure("archive_response_invalid"); }
}

function archiveItem(item, excerpt) {
    requireValue(object(item) && object(item.locator) && encoder.encode(JSON.stringify(item.locator)).length <= 8192);
    const out = {};
    requireValue(typeof item.extraction_id === "string" && /^[1-9][0-9]{0,18}$/.test(item.extraction_id));
    out.extraction_id = item.extraction_id;
    out.unit_key = byteString(item.unit_key, 1024);
    for (const key of ["unit_kind", "source_kind"]) out[key] = string(item[key], 300);
    requireValue(SOURCE_KINDS.includes(item.source_kind));
    out.source_id = byteString(item.source_id, 1024, true);
    out.source_entity_type = byteString(item.source_entity_type, 512, true);
    out.title = string(item.title, 300);
    requireValue(typeof item.title_truncated === "boolean"); out.title_truncated = item.title_truncated;
    out.text = string(item.text, excerpt ? 1600 : 16000);
    requireValue(Number.isSafeInteger(item.text_length) && item.text_length >= [...item.text].length && item.text_length <= 16000 &&
      item.is_excerpt === excerpt && typeof item.text_truncated === "boolean" &&
      item.text_truncated === (item.text_length > [...item.text].length));
    out.text_length = item.text_length; out.text_truncated = item.text_truncated; out.is_excerpt = excerpt;
    out.locator = item.locator;
    for (const key of ["source_created_at", "source_updated_at", "source_version_id"]) out[key] = string(item[key], 300, true);
    for (const key of ["input_blob_sha256", "extraction_output_sha256", "raw_blob_sha256", "text_sha256"]) {
      requireValue((["input_blob_sha256", "raw_blob_sha256"].includes(key) && item[key] === null) || (typeof item[key] === "string" && /^[a-f0-9]{64}$/.test(item[key])));
      out[key] = item[key];
    }
    out.raw_json_pointer = byteString(item.raw_json_pointer, 8192, true);
    requireValue((out.raw_blob_sha256 === null) === (out.raw_json_pointer === null));
    return out;
}

export function archiveSearchResult(value, requestedLimit) {
  requireValue(object(value) && Array.isArray(value.items) && value.items.length <= requestedLimit && value.returned === value.items.length &&
    typeof value.has_more === "boolean" && value.scope === "imported_archive_text" && value.all_versions === true && value.full_archive === false &&
    value.search_mode === "plain_terms" && value.order === "extraction_id_unit_key_asc");
  const items = value.items.map((item) => archiveItem(item, true));
  return { items, returned: items.length, has_more: value.has_more, scope: value.scope, all_versions: true, full_archive: false,
    search_mode: value.search_mode, order: value.order };
}

export function archiveUnitResult(value, input) {
  requireValue(object(value) && value.scope === "imported_archive_text" && value.full_archive === false && value.all_versions === true && typeof value.found === "boolean");
  if (!value.found) {
    requireValue(value.item === null);
    return { found: false, item: null, scope: value.scope, all_versions: true, full_archive: false };
  }
  const item = archiveItem(value.item, false);
  requireValue(item.extraction_id === input.extraction_id && item.unit_key === input.unit_key && !item.text_truncated);
  return { found: true, item, scope: value.scope, all_versions: true, full_archive: false };
}

export function archiveStatusResult(value) {
  requireValue(object(value) && value.scope === "imported_archive_text" && value.all_versions === true && value.full_archive === false &&
    value.storage_files_outside_import_not_searched === true && value.semantic_understanding_measured === false && object(value.counts));
  const counts = {};
  for (const key of ["objects", "object_versions", "extraction_versions", "text_units", "logical_blobs", "attachment_observations", "import_batches", "ingestion_runs"]) {
    requireValue(typeof value.counts[key] === "string" && /^(0|[1-9][0-9]{0,19})$/.test(value.counts[key]));
    counts[key] = value.counts[key];
  }
  let lastRun = null;
  if (value.last_run !== null) {
    const run = value.last_run;
    requireValue(object(run) && ["collecting", "complete", "partial", "failed"].includes(run.status) &&
      object(run.coverage) && encoder.encode(JSON.stringify(run.coverage)).length <= 65536);
    lastRun = { id: string(run.id, 300), status: run.status, started_at: string(run.started_at, 100),
      finished_at: string(run.finished_at, 100, true), coverage: run.coverage };
  }
  return { scope: value.scope, all_versions: true, full_archive: false, counts, last_run: lastRun,
    storage_files_outside_import_not_searched: true, semantic_understanding_measured: false };
}

export async function archiveApi(request, env, route) {
  if (request.method !== "GET") return archiveJson({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  try {
    requireValue(["search", "status", "unit"].includes(route), "not_found", 404);
    const payload = archiveQuery(new URL(request.url), route);
    const configuredKey = env?.SUPABASE_SERVICE_KEY;
    requireValue(configuredKey !== undefined && configuredKey !== null, "archive_key_missing");
    requireValue(typeof configuredKey === "string", "archive_key_invalid");
    // Strip only accidental surrounding whitespace; never rewrite key contents.
    const key = configuredKey.trim();
    requireValue(key.length > 0, "archive_key_missing");
    requireValue(/^sb_secret_[A-Za-z0-9_-]{8,}$/.test(key) || /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(key), "archive_key_invalid");
    const headers = { apikey: key, "Content-Type": "application/json", Accept: "application/json" };
    if (!key.startsWith("sb_secret_")) headers.Authorization = "Bearer " + key;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetch(ORIGIN + "/rest/v1/rpc/" + ({ search: "archive_search_text", status: "archive_search_status", unit: "archive_search_unit" })[route], {
        method: "POST", redirect: "manual", signal: controller.signal,
        headers,
        body: JSON.stringify(payload),
      });
      if (response.status !== 200) {
        try { await response.body?.cancel(); } catch {}
        if (response.status === 401 || response.status === 403) throw new ArchiveFailure("archive_key_rejected");
        if (response.status === 400) throw new ArchiveFailure("invalid_archive_query", 400);
        throw new ArchiveFailure(response.status === 409 ? "archive_not_ready" : "archive_unavailable", response.status === 409 ? 409 : 503);
      }
      const value = await readBounded(response, route === "unit" ? UNIT_RESPONSE_BYTES : route === "status" ? STATUS_RESPONSE_BYTES : MAX_RESPONSE_BYTES);
      const result = route === "search" ? archiveSearchResult(value, payload.result_limit) : route === "unit" ? archiveUnitResult(value, payload) : archiveStatusResult(value);
      if (route === "unit" && result.found) {
        const digest = await crypto.subtle.digest("SHA-256", encoder.encode(result.item.text));
        const textHash = [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
        requireValue(textHash === result.item.text_sha256, "archive_text_integrity");
      }
      return archiveJson(result);
    } finally { clearTimeout(timeout); }
  } catch (error) {
    return archiveJson({ error: error instanceof ArchiveFailure ? error.message : "archive_unavailable" }, error instanceof ArchiveFailure ? error.status : 503);
  }
}
