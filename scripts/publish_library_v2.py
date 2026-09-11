#!/usr/bin/env python3
"""Private content-addressed library. No payload files, public data or raw logs.

Only library:v2:current is mutable. All content, directory nodes, pages and
revision manifests are immutable. Actions concurrency remains the single-writer
boundary: KV itself supplies neither compare-and-swap nor a transaction.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from urllib import parse
import uuid

import publish_library as v1

CURRENT = "library:v2:current"
PREFIX = "library:v2:blob:"
MAX_ARTICLES = 250_000
MAX_SEGMENTS = 100
MAX_VALUE_BYTES = 4 * 1024 * 1024
PAGE_BYTES = 128 * 1024
PAGE_ROWS = 50
BODY_BLOCK_BYTES = 1024 * 1024
TREE_FANOUT = 64
MAX_BUFFER_BYTES = 8 * 1024 * 1024
MAX_ARTICLE_RESPONSE_BYTES = 2 * 1024 * 1024
SUMMARY_SOURCES_BYTES = 16 * 1024
KINDS = ("knowledge", "supplier", "price", "component")
ORDERED_SQL = f"""SELECT {v1.ARTICLE_COLUMNS} FROM public.lib_knowledge
WHERE researched_by = %s AND sources->>'publication_approved' = 'true'
ORDER BY sources->>'importer_id' COLLATE \"C\", id LIMIT 250001"""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return v1.encode(value)


def kind(row):
    value = row["sources"].get("kind") if isinstance(row["sources"], dict) else None
    return value if value in KINDS else "knowledge"


def summary(row):
    sources = row["sources"]
    result_sources = {"kind": kind(row)}
    truncated = False
    if isinstance(sources, dict):
        for key in ("typedfields", "supplier_fields", "price_fields", "component_fields", "references",
                    "review_status", "open_questions", "source_date", "origin", "importer_id"):
            if key not in sources:
                continue
            candidate = {**result_sources, key: copy.deepcopy(sources[key])}
            if len(encode(candidate)) <= SUMMARY_SOURCES_BYTES:
                result_sources = candidate
            else:
                truncated = True
        truncated |= bool(set(sources) - set(result_sources))
    else:
        candidate = {**result_sources, "references": sources}
        if len(encode(candidate)) <= SUMMARY_SOURCES_BYTES:
            result_sources = candidate
        else:
            truncated = True
    return {key: row[key] for key in ("id", "segment_id", "title", "topic", "confidence", "updated_at")} | {
        "excerpt": row["body"][:1200], "sources": result_sources, "sources_truncated": truncated}


def search_text(row):
    # Full strings, including body and all structured sources. The worker uses
    # the same Unicode lowercase operation; no field is silently truncated.
    strings = [row["title"], row["topic"], row["body"]]
    def walk(value):
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, dict):
            for key in sorted(value):
                strings.append(key)
                walk(value[key])
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif value is not None:
            strings.append(str(value).lower())
    walk(row["sources"])
    return "\n".join(strings).lower()


class Cloudflare(v1.Cloudflare):
    def preserve(self, namespace, key, raw):
        old = self.get(namespace, key)
        v1.require(old is None or old == raw, "HISTORY_REVISION_CONFLICT")
        if old == raw:
            return  # This exact GET is already the immutable object's readback.
        self.put(namespace, key, raw)
        self.verify(namespace, key, raw)

    def value_path(self, namespace, key):
        if key == CURRENT or key.startswith(PREFIX) or key.startswith("library:v2:revision:"):
            v1.require(isinstance(namespace, str) and v1.CF_ID.fullmatch(namespace), "INVALID_KV_BINDING")
            if key.startswith(PREFIX):
                suffix = key[len(PREFIX):]
                v1.require(len(suffix) == 64 and all(c in "0123456789abcdef" for c in suffix), "INVALID_KV_KEY")
            elif key.startswith("library:v2:revision:"):
                v1.stable_id(key[len("library:v2:revision:"):])
            return f"/storage/kv/namespaces/{namespace}/values/{parse.quote(key, safe='')}"
        return super().value_path(namespace, key)


class Store:
    def __init__(self, cf, namespace):
        self.cf, self.namespace = cf, namespace
        self.writes = 0

    def put(self, value):
        raw = encode(value)
        ref = {"sha256": digest(raw), "bytes": len(raw)}
        self.cf.preserve(self.namespace, PREFIX + ref["sha256"], raw)
        self.writes += 1
        return ref

    def get(self, ref):
        v1.require(isinstance(ref, dict) and isinstance(ref.get("sha256"), str) and
                   0 < ref.get("bytes", 0) <= MAX_VALUE_BYTES, "INVALID_BLOB_REFERENCE")
        raw = self.cf.get(self.namespace, PREFIX + ref["sha256"])
        v1.require(raw is not None and len(raw) == ref["bytes"] and digest(raw) == ref["sha256"],
                   "BLOB_INTEGRITY_FAILED")
        return v1.decode(raw)


def tree(store, leaves, category):
    if not leaves:
        return None
    level = leaves
    while len(level) > 1:
        next_level = []
        for start in range(0, len(level), TREE_FANOUT):
            children = level[start:start + TREE_FANOUT]
            ref = store.put({"type": "branch", "category": category, "children": children})
            next_level.append({**ref, "count": sum(x["count"] for x in children),
                               "first": children[0]["first"], "last": children[-1]["last"]})
        level = next_level
    return level[0]


def entries(store, ref, category, depth=0):
    if ref is None:
        return
    v1.require(depth < 8, "TREE_DEPTH_LIMIT")
    node = store.get(ref)
    v1.require(node.get("category") == category, "TREE_CATEGORY_MISMATCH")
    v1.require(isinstance(ref.get("count"), int) and 0 < ref["count"] <= MAX_ARTICLES and
               isinstance(ref.get("first"), str) and isinstance(ref.get("last"), str) and
               ref["first"] <= ref["last"], "INVALID_TREE")
    if node.get("type") == "branch":
        children = node.get("children")
        v1.require(isinstance(children, list) and 0 < len(children) <= TREE_FANOUT, "INVALID_TREE")
        v1.require(sum(c["count"] for c in children) == ref["count"] and
                   children[0]["first"] == ref["first"] and children[-1]["last"] == ref["last"] and
                   all(a["last"] < b["first"] for a, b in zip(children, children[1:])), "INVALID_TREE")
        for child in children:
            yield from entries(store, child, category, depth + 1)
    else:
        v1.require(node.get("type") == "leaf" and isinstance(node.get("items"), list), "INVALID_TREE")
        items = node["items"]
        v1.require(len(items) == ref["count"] <= PAGE_ROWS and items[0]["id"] == ref["first"] and
                   items[-1]["id"] == ref["last"] and
                   all(a["id"] < b["id"] for a, b in zip(items, items[1:])), "INVALID_TREE")
        yield from node["items"]


class Builder:
    def __init__(self, store, segments):
        self.store = store
        self.segments = {v1.stable_id(x["id"]): copy.deepcopy(x) for x in segments}
        v1.require(len(self.segments) == len(segments) <= MAX_SEGMENTS, "INVALID_SEGMENTS")
        self.buffers, self.sizes, self.leaves = {}, {}, defaultdict(list)
        self.buffer_bytes = 0
        self.counts = defaultdict(lambda: defaultdict(int))
        self.confidence_counts = defaultdict(lambda: defaultdict(int))
        self.count, self.last_id = 0, None
        self.body_rows, self.body_bytes = [], 0

    def flush_bodies(self):
        if not self.body_rows:
            return
        ref = self.store.put({"type": "articles", "items": self.body_rows})
        for index, row in enumerate(self.body_rows):
            self.append(("directory", "", ""), {"id": row["id"], "block": ref,
                "index": index, "managed": v1.managed(row)})
        self.body_rows, self.body_bytes = [], 0

    def flush(self, group):
        items = self.buffers.pop(group, [])
        self.buffer_bytes -= self.sizes.pop(group, 0)
        if not items:
            return
        ref = self.store.put({"type": "leaf", "category": group[0], "items": items})
        self.leaves[group].append({**ref, "count": len(items), "first": items[0]["id"], "last": items[-1]["id"]})

    def append(self, group, item):
        size = len(encode(item)) + 1
        if self.sizes.get(group, 0) + size > PAGE_BYTES or len(self.buffers.get(group, [])) >= PAGE_ROWS:
            self.flush(group)
        self.buffers.setdefault(group, []).append(item)
        self.sizes[group] = self.sizes.get(group, 0) + size
        self.buffer_bytes += size
        while self.buffer_bytes > MAX_BUFFER_BYTES:
            self.flush(max(self.sizes, key=self.sizes.get))

    def add(self, row):
        # Validate but preserve every original field and timestamp spelling on
        # retained historical articles; validation is not a source rewrite.
        normalized = v1.article(row)
        v1.require(len(encode({"revision": "0" * 160, "article": row})) <= MAX_ARTICLE_RESPONSE_BYTES,
                   "ARTICLE_REQUIRES_PAGED_BODY")
        v1.require(row["segment_id"] in self.segments, "UNKNOWN_SEGMENT")
        v1.require(self.last_id is None or self.last_id < row["id"], "DUPLICATE_OR_UNSORTED_ID")
        self.last_id = row["id"]
        self.count += 1
        v1.require(self.count <= MAX_ARTICLES, "ARTICLE_LIMIT")
        size = len(encode(row)) + 1
        if len(self.body_rows) >= PAGE_ROWS or self.body_bytes + size > BODY_BLOCK_BYTES:
            self.flush_bodies()
        self.body_rows.append(row)
        self.body_bytes += size
        item = summary(normalized)
        seg, k = row["segment_id"], kind(normalized)
        self.append(("catalog", seg, k), item)
        self.append(("search", seg, k), {"id": row["id"], "summary": item, "text": search_text(normalized)})
        self.counts[seg][k] += 1
        self.confidence_counts[seg][normalized["confidence"]] += 1

    def finish(self, revision, published_at):
        self.flush_bodies()
        for group in list(self.buffers):
            self.flush(group)
        roots = {group: tree(self.store, leaves, group[0]) for group, leaves in self.leaves.items()}
        segments = []
        for seg, source in sorted(self.segments.items()):
            counts = {k: self.counts[seg][k] for k in KINDS}
            segments.append({**source, "article_count": sum(counts.values()), "counts_by_kind": counts,
                "counts_by_confidence": dict(sorted(self.confidence_counts[seg].items())),
                "indexes": {k: {c: roots.get((c, seg, k)) for c in ("catalog", "search")} for k in KINDS}})
        confidence = defaultdict(int)
        for counts in self.confidence_counts.values():
            for name, count in counts.items():
                confidence[name] += count
        return {"version": 2, "revision": revision, "published_at": published_at,
                "article_count": self.count, "segments": segments,
                "counts_by_confidence": dict(sorted(confidence.items())),
                "directory": roots.get(("directory", "", "")), "search_version": "unicode-lower-substring-v1"}


class Database(v1.Database):
    @contextmanager
    def stream(self):
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(v1.SEGMENTS_SQL)
                rows = cursor.fetchmany(MAX_SEGMENTS + 1)
                v1.require(len(rows) <= MAX_SEGMENTS, "SEGMENT_LIMIT")
                segments = [{"id": v1.stable_id(r[0]), "name": v1.string(r[1], 300),
                             "note": v1.string(r[2], 10000, True)} for r in rows]
            # Server cursor bounds client memory; the repeatable-read transaction
            # supplies one source snapshot for all fetches.
            with connection.cursor(name="private_library_v2") as cursor:
                cursor.itersize = 50
                cursor.execute(ORDERED_SQL, (v1.MANAGER,))
                def source():
                    seen = 0
                    while True:
                        rows = cursor.fetchmany(50)
                        if not rows:
                            return
                        for row in rows:
                            seen += 1
                            v1.require(seen <= MAX_ARTICLES, "ARTICLE_LIMIT")
                            normalized = v1.db_article(row)
                            v1.require(v1.managed(normalized), "UNAPPROVED_DATABASE_ROW")
                            yield normalized
                yield segments, source()
        except v1.PublishError:
            raise
        except Exception:
            raise v1.PublishError("DATABASE_READ_FAILED") from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()


def old_rows(store, old_manifest, old_v1):
    if old_manifest is None:
        for row in sorted(old_v1["articles"], key=lambda x: x["id"]):
            yield row["id"], row, None, v1.managed(row)
        return
    for entry in entries(store, old_manifest["directory"], "directory"):
        yield entry["id"], None, entry, entry["managed"]


def stored_article(store, entry, cache=None):
    ref = entry["block"]
    if cache is not None and cache.get("sha") == ref["sha256"]:
        block = cache["block"]
    else:
        block = store.get(ref)
        if cache is not None:
            cache.update(sha=ref["sha256"], block=block)
    index = entry["index"]
    v1.require(block.get("type") == "articles" and isinstance(block.get("items"), list) and
               type(index) is int and 0 <= index < len(block["items"]) <= PAGE_ROWS, "INVALID_BODY_BLOCK")
    row = block["items"][index]
    v1.require(row.get("id") == entry["id"], "ARTICLE_ID_MISMATCH")
    return row


def find_entry(store, ref, identity, depth=0):
    if ref is None or identity < ref["first"] or identity > ref["last"]:
        return None
    v1.require(depth < 8, "TREE_DEPTH_LIMIT")
    node = store.get(ref)
    v1.require(node.get("category") == "directory", "TREE_CATEGORY_MISMATCH")
    if node.get("type") == "leaf":
        # Reuse complete leaf structural/identity validation, not a guessed slot.
        return next((e for e in entries(store, ref, "directory") if e["id"] == identity), None)
    children = node.get("children")
    v1.require(node.get("type") == "branch" and isinstance(children, list) and
               0 < len(children) <= TREE_FANOUT and sum(c["count"] for c in children) == ref["count"] and
               children[0]["first"] == ref["first"] and children[-1]["last"] == ref["last"] and
               all(a["last"] < b["first"] for a, b in zip(children, children[1:])), "INVALID_TREE")
    child = next((c for c in children if c["first"] <= identity <= c["last"]), None)
    return find_entry(store, child, identity, depth + 1)


def preflight_drafts(store, drafts, old_manifest, legacy):
    legacy_by_id = {r["id"]: r for r in legacy["articles"]} if old_manifest is None else {}
    for draft in drafts:
        row = draft["article"]
        v1.require(len(encode({"revision": "0" * 160, "article": row})) <= MAX_ARTICLE_RESPONSE_BYTES,
                   "ARTICLE_REQUIRES_PAGED_BODY")
        # Capacity of the full-source search representation is also checked
        # before permitting the narrowly authorized draft insert.
        encode({"type": "leaf", "category": "search", "items": [
            {"id": row["id"], "summary": summary(row), "text": search_text(row)}]})
        if old_manifest:
            entry = find_entry(store, old_manifest["directory"], row["id"])
            v1.require(entry is None or entry["managed"] is True, "MANAGED_ID_CONFLICT")
        else:
            old = legacy_by_id.get(row["id"])
            v1.require(old is None or v1.managed(old), "MANAGED_ID_CONFLICT")


def reconcile(store, builder, previous, incoming, expected_drafts):
    # Merge two streams ordered by ASCII stable ID. Retain unrelated and missing
    # historical rows; replace only explicit managed IDs, as the v1 contract did.
    old = iter(previous)
    sentinel = object()
    prior = next(old, sentinel)
    checked_drafts = set()
    last_incoming = None
    old_block_cache = {}
    for row in incoming:
        v1.require(last_incoming is None or row["id"] > last_incoming, "DUPLICATE_OR_UNSORTED_ID")
        last_incoming = row["id"]
        while prior is not sentinel and prior[0] < row["id"]:
            old_row = prior[1] or stored_article(store, prior[2], old_block_cache)
            builder.add(old_row)
            prior = next(old, sentinel)
        if prior is not sentinel and prior[0] == row["id"]:
            v1.require(prior[3] is True, "MANAGED_ID_CONFLICT")
            prior = next(old, sentinel)
        if row["id"] in expected_drafts:
            v1.require(encode(row) == encode(expected_drafts[row["id"]]), "DATABASE_READBACK_MISMATCH")
            checked_drafts.add(row["id"])
        builder.add(row)
    while prior is not sentinel:
        old_row = prior[1] or stored_article(store, prior[2], old_block_cache)
        builder.add(old_row)
        prior = next(old, sentinel)
    v1.require(checked_drafts == set(expected_drafts), "DATABASE_READBACK_MISMATCH")


def run(db, cf, now=None):
    namespace = cf.namespace()
    store = Store(cf, namespace)
    old_raw = cf.get(namespace, CURRENT)
    old_manifest = None
    if old_raw is not None:
        pointer = v1.decode(old_raw)
        v1.require(pointer.get("version") == 2, "INVALID_VERSION")
        old_manifest = store.get(pointer["manifest"])
        v1.require(old_manifest["revision"] == pointer["revision"], "REVISION_MISMATCH")
    v1_raw = cf.get(namespace, v1.CURRENT_KEY)
    legacy = v1.snapshot(v1_raw)
    # Segment names and draft FK are read in a short readonly transaction.
    with db.stream() as (segments, _):
        metrics = {}
        drafts = v1.pending_drafts(cf, namespace, segments, metrics)
    preflight_drafts(store, drafts, old_manifest, legacy)
    for draft in drafts:
        v1.require(cf.get(namespace, draft["key"]) == draft["raw"], "DRAFT_CHANGED")
    db.insert_drafts([d["article"] for d in drafts])
    revision = str(uuid.uuid4())
    published_at = v1.timestamp(now or datetime.now(timezone.utc))
    with db.stream() as (segments, incoming):
        old_segments = old_manifest["segments"] if old_manifest else legacy["segments"]
        merged_segments = {x["id"]: {k: v for k, v in x.items() if k not in
                           ("indexes", "counts_by_kind", "counts_by_confidence", "article_count")} for x in old_segments}
        merged_segments.update({x["id"]: x for x in segments})
        builder = Builder(store, list(merged_segments.values()))
        reconcile(store, builder, old_rows(store, old_manifest, legacy), incoming,
                  {d["article"]["id"]: d["article"] for d in drafts})
        manifest = builder.finish(revision, published_at)
    # No new current or published draft appears until every new CAS object was
    # written and read back exactly. Unreferenced failed-build blobs are retained.
    comparable = lambda m: {k: v for k, v in m.items() if k not in ("revision", "published_at")}
    unchanged = old_manifest is not None and encode(comparable(old_manifest)) == encode(comparable(manifest))
    v1.require(cf.get(namespace, CURRENT) == old_raw, "CURRENT_LIBRARY_CHANGED")
    v1.require(cf.get(namespace, v1.CURRENT_KEY) == v1_raw, "LEGACY_LIBRARY_CHANGED")
    if unchanged:
        manifest = old_manifest
        cf.verify(namespace, CURRENT, old_raw)
    else:
        if legacy["revision"]:
            cf.preserve(namespace, "library:history:" + legacy["revision"], v1_raw)
        pointer_raw = encode({"version": 2, "revision": revision, "published_at": published_at,
                              "manifest": store.put(manifest)})
        cf.preserve(namespace, "library:v2:revision:" + revision, pointer_raw)
        v1.require(cf.get(namespace, CURRENT) == old_raw, "CURRENT_LIBRARY_CHANGED")
        v1.require(cf.get(namespace, v1.CURRENT_KEY) == v1_raw, "LEGACY_LIBRARY_CHANGED")
        cf.put(namespace, CURRENT, pointer_raw)
        cf.verify(namespace, CURRENT, pointer_raw)
    for draft in drafts:
        v1.require(cf.get(namespace, draft["key"]) == draft["raw"], "DRAFT_CHANGED")
        completed = {**draft["envelope"], "status": "published", "published_at": published_at,
                     "publication_revision": manifest["revision"]}
        cf.put(namespace, draft["key"], encode(completed))
        cf.verify(namespace, draft["key"], encode(completed))
    return {"ok": True, "version": 2, "changed": not unchanged, "articles": manifest["article_count"],
            "segments": len(manifest["segments"]), "drafts_published": len(drafts),
            "draft_keys_deferred": metrics["draft_keys_deferred"],
            "next_manual_run_required": bool(metrics["draft_keys_deferred"])}


def main(environ=None):
    env = os.environ if environ is None else environ
    try:
        result = run(Database(env.get("SUPABASE_DB_URL")),
                     Cloudflare(env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_API_TOKEN")))
    except v1.PublishError as failure:
        result = {"ok": False, "error": str(failure)}
    except KeyboardInterrupt:
        result = {"ok": False, "error": "INTERRUPTED"}
    except Exception:
        result = {"ok": False, "error": "PUBLISH_FAILED"}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
