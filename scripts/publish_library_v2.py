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
import re
import os
import sys
import time
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
MAX_RELATION_EDGES = 25_000
MAX_COMPONENT_RELATIONS = 512
MAX_RELATION_BYTES = 8 * 1024 * 1024
KINDS = ("knowledge", "supplier", "price", "component")
# Exact source-family labels only. Parity with the reader is tested; this adds
# search aliases without rewriting canonical titles, sources or full records.
COMPONENT_FAMILIES = {
    "Однорядный радиальный шариковый": "Однорядный радиальный шариковый подшипник",
    "Стальной профиль": "Стальной профиль", "Приводная цепь": "Приводная цепь",
    "Втулочная приводная цепь": "Втулочная приводная цепь", "Пластинчатая цепь": "Пластинчатая цепь",
    "single_row_ball": "Однорядный шариковый подшипник",
    "double_row_ball": "Двухрядный шариковый подшипник",
    "electric_motor": "Электродвигатель", "programmable_controller": "Программируемый контроллер",
    "controller_io_module": "Модуль ввода-вывода контроллера", "motion_controller": "Контроллер управления движением",
    "hydraulic_gear_pump": "Шестерённый гидравлический насос",
    "hydraulic_gear_pump_stage": "Секция шестерённого гидравлического насоса",
    "spherical_roller": "Сферический роликовый подшипник",
    "cylindrical_roller": "Цилиндрический роликовый подшипник",
    "tapered_roller": "Конический роликовый подшипник",
    "deep_groove_ball": "Радиальный шариковый подшипник",
    "angular_contact_ball": "Радиально-упорный шариковый подшипник",
    "self_aligning_ball": "Самоустанавливающийся шариковый подшипник",
    "thrust_ball": "Упорный шариковый подшипник",
}
# These are explicit catalogue family fields, qualified by the stated OEM.
# In particular, no prefix/substring of a part number is used as a family.
OEM_COMPONENT_FAMILIES = {
    "Dormer Pramet": {
        "R023": "Короткое твердосплавное сверло",
        "R003": "Твердосплавное сверло",
        "RS403": "Твердосплавное сверло",
        "RC403": "Твердосплавное сверло",
        "RS405": "Твердосплавное сверло",
        "RC405": "Твердосплавное сверло",
        "RC408": "Твердосплавное сверло",
        "RC305": "Твердосплавное микросверло",
        "RC412": "Твердосплавное сверло для глубоких отверстий",
        "RC416": "Твердосплавное сверло для глубоких отверстий",
        "RC420": "Твердосплавное сверло для глубоких отверстий",
        "RC4P": "Твердосплавное пилотное сверло",
        "R122": "Твердосплавное сверло для засверливания",
        "R123": "Твердосплавное сверло для засверливания",
        "R125": "Твердосплавное сверло для засверливания",
        "R6011": "Твердосплавное сверло для засверливания",
        "R200": "Твердосплавное центровочное сверло",
        "R7131": "Твердосплавное ступенчатое сверло"
    },
    "Pentair": {
        "PENTEK 3G SLIM LINE FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK 3G STANDARD SERIES FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK ALL NATURAL FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK BIG BLUE HEAVY DUTY FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK BIG BLUE WITH DRAIN FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK BIG CLEAR HEAVY DUTY FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK BIG WHITE FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK BIG WHITE WITH BYPASS FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK CARBON BLOCK CARTRIDGES": "Фильтрующий картридж",
        "PENTEK CARBON BLOCK MULTI-MEDIA CARTRIDGES": "Фильтрующий картридж",
        "PENTEK CERAMIC CARTRIDGE": "Фильтрующий картридж",
        "PENTEK CHLORAMINE REDUCTION CARBON CARTRIDGES": "Фильтрующий картридж",
        "PENTEK COCONUT SHELL GRANULAR ACTIVATED CARBON CARTRIDGES": "Фильтрующий картридж",
        "PENTEK COCONUT-BASED CARBON BLOCK CARTRIDGES": "Фильтрующий картридж",
        "PENTEK COUNTER TOP SLIM LINE SERIES FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK DIAMOND FLOW CARTRIDGES": "Фильтрующий картридж",
        "PENTEK DUAL PURPOSE POWDER-ACTIVATED CARBON CARTRIDGE": "Фильтрующий картридж",
        "PENTEK DUAL PURPOSE POWDER-ACTIVATED CARBON CARTRIDGES": "Фильтрующий картридж",
        "PENTEK ELPC ELECTROPLATING CARBON CARTRIDGES": "Фильтрующий картридж",
        "PENTEK FLAT CAP SLIM LINE FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK GRADIENT DENSITY CARTRIDGES": "Фильтрующий картридж",
        "PENTEK GRANULAR ACTIVATED CARBON CARTRIDGES": "Фильтрующий картридж",
        "PENTEK HEXAMETAPHOSPHATE CRYSTAL CARTRIDGES": "Фильтрующий картридж",
        "PENTEK HIGH TEMPERATURE FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK MICROGUARD SERIES CARTRIDGES": "Фильтрующий картридж",
        "PENTEK MIXED BED DEIONIZATION CARTRIDGES": "Фильтрующий картридж",
        "PENTEK MODIFIED EPSILON CARBON BLOCK CARTRIDGES": "Фильтрующий картридж",
        "PENTEK MODIFIED MOLDED BLOCK CARTRIDGE": "Фильтрующий картридж",
        "PENTEK MODIFIED MOLDED BLOCK CARTRIDGES": "Фильтрующий картридж",
        "PENTEK MODIFIED MOLDED CARBON BLOCK CARTRIDGES": "Фильтрующий картридж",
        "PENTEK MPST 1.5 STAINLESS STEEL FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK NON-CELLULOSE CARBON-IMPREGNATED PLEATED CARTRIDGES": "Фильтрующий картридж",
        "PENTEK PBH BAG FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK PLEATED CELLULOSE CARTRIDGES": "Фильтрующий картридж",
        "PENTEK PLEATED CELLULOSE POLYESTER CARTRIDGES": "Фильтрующий картридж",
        "PENTEK PLEATED POLYESTER CARTRIDGES": "Фильтрующий картридж",
        "PENTEK POLYDEPTH CARTRIDGES": "Фильтрующий картридж",
        "PENTEK POLYPROPYLENE STRING WOUND CARTRIDGES": "Фильтрующий картридж",
        "PENTEK POLYPROPYLENE STRING-WOUND CARTRIDGES": "Фильтрующий картридж",
        "PENTEK POLYPROPYLENE WOUND CARTRIDGES": "Фильтрующий картридж",
        "PENTEK RADIAL FLOW IRON REDUCTION CARTRIDGE": "Фильтрующий картридж",
        "PENTEK SCBC-10 ANTIMICROBIAL CARBON BLOCK CARTRIDGE": "Фильтрующий картридж",
        "PENTEK SLIM LINE FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK SPECIALTY GRANULAR ACTIVATED CARBON/PHOSPHATE CARTRIDGE": "Фильтрующий картридж",
        "PENTEK SPUN-BONDED POLYPROPYLENE CARTRIDGES": "Фильтрующий картридж",
        "PENTEK ST SERIES STAINLESS STEEL FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK ST-BC SERIES STAINLESS STEEL FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK STANDARD FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK STANDARD FLAT CAP FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK UDS/DBC SERIES CARTRIDGES": "Фильтрующий картридж",
        "PENTEK VALVE-IN-HEAD SERIES FILTER HOUSINGS": "Корпус фильтра",
        "PENTEK WATER SOFTENER CARTRIDGES": "Фильтрующий картридж",
        "PENTEK FILTER BAGS": "Фильтрующий мешок",
        "PENTAIR THIN LAYER COMPOSITE MEMBRANES": "Тонкоплёночная композитная мембрана",
        "PENTEK ULTRAVIOLET SYSTEMS": "Ультрафиолетовая система",
        "PENTEK REVERSE OSMOSIS DRINKING WATER SYSTEM": "Система обратного осмоса"
    },
    "Danfoss": {
        "XB04-1": "Паяный пластинчатый теплообменник",
        "XB04-2": "Паяный пластинчатый теплообменник",
        "XB10-1": "Паяный пластинчатый теплообменник",
        "XB10-2": "Паяный пластинчатый теплообменник",
        "XB20-1": "Паяный пластинчатый теплообменник",
        "XB20-2": "Паяный пластинчатый теплообменник",
        "XB24-1": "Паяный пластинчатый теплообменник",
        "XB30-1": "Паяный пластинчатый теплообменник",
        "XB30-2": "Паяный пластинчатый теплообменник",
        "XB51H-1": "Паяный пластинчатый теплообменник",
        "XB51H-1 SB": "Паяный пластинчатый теплообменник",
        "XB51H-2": "Паяный пластинчатый теплообменник",
        "XB51L-1": "Паяный пластинчатый теплообменник",
        "XB51L-1 SB": "Паяный пластинчатый теплообменник",
        "XB51L-2": "Паяный пластинчатый теплообменник",
        "XB70H-1": "Паяный пластинчатый теплообменник",
        "XB70L-1": "Паяный пластинчатый теплообменник",
        "XB70M-1": "Паяный пластинчатый теплообменник"
    },
    "Tsurumi": {
        "KTZ": "Погружной дренажный насос"
    },
    "Swagelok": {
        "40G": "Шаровой кран",
        "40GX": "Шаровой кран"
    }
}

PENTAIR_ACCESSORY_FAMILIES = [
    "PENTEK 3G STANDARD SERIES FILTER HOUSINGS",
    "PENTEK ALL NATURAL FILTER HOUSINGS",
    "PENTEK BIG BLUE HEAVY DUTY FILTER HOUSINGS",
    "PENTEK BIG BLUE WITH DRAIN FILTER HOUSINGS",
    "PENTEK BIG CLEAR HEAVY DUTY FILTER HOUSINGS",
    "PENTEK BIG WHITE FILTER HOUSINGS",
    "PENTEK BIG WHITE WITH BYPASS FILTER HOUSINGS",
    "PENTEK COUNTER TOP SLIM LINE SERIES FILTER HOUSINGS",
    "PENTEK SLIM LINE FILTER HOUSINGS",
    "PENTEK ST SERIES STAINLESS STEEL FILTER HOUSINGS",
    "PENTEK ST-BC SERIES STAINLESS STEEL FILTER HOUSINGS",
    "PENTEK STANDARD FILTER HOUSINGS",
    "PENTEK VALVE-IN-HEAD SERIES FILTER HOUSINGS",
    "Pentek water filtration"
]

PENTAIR_DESCRIPTION_LABELS = {
    "Thin Film Membrane": "Тонкоплёночная мембрана",
    "Cartridge Set": "Комплект картриджей",
    "RO Replacement Cartridge Set": "Комплект сменных картриджей для обратного осмоса",
    "Spanner Wrench": "Ключ для корпуса фильтра",
    "Faucet": "Кран",
    "Auto Shut-Off Valve": "Автоматический запорный клапан",
    "ST Gasket , BUNA-N": "Прокладка ST",
    "ST Gasket, Teflon": "Прокладка ST",
    "ST Gasket, Viton": "Прокладка ST",
    "ST Gasket, Silicone": "Прокладка ST",
    "Gasket for STBC Series": "Прокладка STBC",
    "Two-Housing Bracket": "Кронштейн для двух корпусов",
    "Three-Housing Bracket": "Кронштейн для трёх корпусов",
    "Housing Stand": "Подставка для корпуса",
    "ST Centering Spring": "Центрирующая пружина ST"
}

VALVE_FLOW_LABELS = {
    "two_way_straight": "Арматура: двухходовая прямоточная",
    "two_way_straight_shutoff": "Арматура: двухходовая прямоточная запорная",
    "three_way_switching": "Арматура: трёхходовая переключающая"
}


def known_component_family(fields):
    if not isinstance(fields, dict) or not isinstance(fields.get("family"), str):
        return ""
    family, oem = fields["family"], fields.get("oem")
    qualified = OEM_COMPONENT_FAMILIES.get(oem, {}) if isinstance(oem, str) else {}
    return qualified.get(family) or COMPONENT_FAMILIES.get(family, "")


def component_type_label(sources, segment_id=None):
    if not isinstance(sources, dict):
        return ""
    fields = sources.get("component_fields")
    if not isinstance(fields, dict):
        return ""
    typed = sources.get("typedfields", {})
    spec = typed.get("specification", {}) if isinstance(typed, dict) else {}
    spec = spec if isinstance(spec, dict) else {}
    values = spec.get("catalogue_fields_as_printed", {})
    values = values if isinstance(values, dict) else {}
    description = values.get("DESCRIPTION")
    description = description if isinstance(description, str) else ""
    oem = fields.get("oem") if isinstance(fields.get("oem"), str) else ""
    family = fields.get("family") if isinstance(fields.get("family"), str) else ""
    if oem == "Pentair":
        if segment_id != "water":
            return ""
        families = OEM_COMPONENT_FAMILIES.get("Pentair", {})
        if fields.get("is_accessory") is True:
            if family not in PENTAIR_ACCESSORY_FAMILIES:
                return ""
            # This field contains only a shared, non-conflicting table value.
            # Never choose the first of the original per-observation versions.
            label = PENTAIR_DESCRIPTION_LABELS.get(description, "Принадлежность системы фильтрации")
            return label + (" · " + description if description else "")
        if fields.get("is_accessory") is not False:
            return ""
        if family == "PENTEK QUICK-CHANGE FILTRATION SYSTEMS":
            return "Сменный картридж фильтра" if isinstance(values.get("CARTRIDGE COLOR"), str) and values["CARTRIDGE COLOR"] else ""
        if family == "Pentek water filtration":
            return PENTAIR_DESCRIPTION_LABELS.get(description, "")
        return families.get(family, "")
    if oem == "Swagelok" and segment_id == "valves":
        if spec.get("valve_type") == "ball":
            return "Шаровой кран"
        if family in OEM_COMPONENT_FAMILIES["Swagelok"]:
            return OEM_COMPONENT_FAMILIES["Swagelok"][family]
        flow = spec.get("flow_pattern")
        return VALVE_FLOW_LABELS.get(flow, "") if isinstance(flow, str) else ""
    scoped = {"Dormer Pramet": "welding", "Danfoss": "heat", "Tsurumi": "pumps", "Swagelok": "valves"}
    if oem in scoped and family in OEM_COMPONENT_FAMILIES.get(oem, {}):
        if segment_id != scoped[oem]:
            return ""
    return known_component_family(fields)
ORDERED_SQL = f"""SELECT {v1.ARTICLE_COLUMNS} FROM public.lib_knowledge
WHERE researched_by = %s AND sources->>'publication_approved' = 'true'
ORDER BY sources->>'importer_id' COLLATE \"C\", id LIMIT 250001"""

# Project identities and source coordinates only, never supplier/article bodies.
# Both sides must be approved rows in the same repeatable-read transaction.
RELATIONS_SQL = """WITH edges AS MATERIALIZED (
 SELECT s.id AS supplier_db_id, s.sources->>'importer_id' AS supplier_id,
 COALESCE(NULLIF(s.sources->'supplier_fields'->>'name',''),s.title) AS name,
 e.link, s.sources->'references' AS refs
 FROM public.lib_knowledge s CROSS JOIN LATERAL jsonb_array_elements(
 CASE WHEN jsonb_typeof(s.sources->'candidate_position_links')='array'
 THEN s.sources->'candidate_position_links' ELSE '[]'::jsonb END) e(link)
 WHERE s.researched_by=%s AND s.sources->>'publication_approved'='true'
 AND s.sources->>'kind'='supplier'
 ORDER BY s.sources->>'importer_id' COLLATE "C",s.id,e.link->>'article_id',e.link->>'source_pointer'
 LIMIT %s
), component_ids AS MATERIALIZED (
 SELECT id, sources->>'importer_id' AS importer_id,
 sources->'component_fields'->>'part_number' AS part_number
 FROM public.lib_knowledge
 WHERE researched_by=%s AND sources->>'publication_approved'='true'
 AND sources->>'kind'='component'
)
SELECT e.supplier_db_id,e.supplier_id,e.name,e.link,t.id,
 t.importer_id,t.part_number,
 (SELECT jsonb_agg(jsonb_build_object('sha256',r->>'sha256','url',r->>'url',
 'json_pointer',e.link->>'source_pointer','repository_path',r->>'repository_path')) FROM jsonb_array_elements(
 CASE WHEN jsonb_typeof(e.refs)='array' THEN e.refs ELSE '[]'::jsonb END) r
 WHERE r->>'repository_path'='zip/data/positions.json' AND
 (r->'locator'->>'json_pointer'=e.link->>'source_pointer' OR
 CASE WHEN jsonb_typeof(r->'locator'->'json_pointers')='array'
 THEN r->'locator'->'json_pointers' ? (e.link->>'source_pointer') ELSE false END))
FROM edges e LEFT JOIN component_ids t ON t.importer_id=e.link->>'article_id'
ORDER BY e.supplier_id COLLATE "C",e.supplier_db_id,e.link->>'article_id',e.link->>'source_pointer',t.id
LIMIT %s"""


def database_identity(value):
    """Canonical internal row identity, separate from the public importer ID."""
    if type(value) is int:
        v1.require(0 < value <= 9223372036854775807, "INVALID_RELATION_DATABASE_ID")
        return ("bigint", value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return database_identity(int(value))
    if isinstance(value, uuid.UUID):
        return ("uuid", str(value))
    if isinstance(value, str) and re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
        return ("uuid", str(uuid.UUID(value)))
    raise v1.PublishError("INVALID_RELATION_DATABASE_ID")


def supplier_relations(projection):
    """Reverse explicit article-ID edges; SKU alone never creates a relation."""
    result, identities = defaultdict(list), {}
    metrics = {"edges": 0, "linked": 0, "missing_target": 0, "missing_evidence": 0, "duplicate_edges": 0}
    seen, byte_count, projection_bytes = set(), 0, 0
    for supplier_db_id, supplier_id, name, edge, target_db_id, target_id, part_number, evidence in projection:
        metrics["edges"] += 1
        v1.require(metrics["edges"] <= MAX_RELATION_EDGES, "RELATION_EDGE_LIMIT")
        projection_bytes += len(encode([str(supplier_db_id), supplier_id, name, edge,
                                       str(target_db_id), target_id, part_number, evidence]))
        v1.require(projection_bytes <= MAX_RELATION_BYTES, "RELATION_BYTE_LIMIT")
        supplier_id = v1.stable_id(supplier_id)
        v1.require(isinstance(edge, dict), "INVALID_RELATION_EDGE")
        requested_id = v1.stable_id(edge.get("article_id"))
        # Stable public article identity and internal database identity have different roles.
        # Duplicate stable IDs must fail even if they yield different positions.
        v1.require(supplier_db_id is not None, "INVALID_RELATION_DATABASE_ID")
        for identity, database_id in ((supplier_id, supplier_db_id), (target_id, target_db_id)):
            if database_id is None:
                continue
            database_id = database_identity(database_id)
            v1.require(identity not in identities or identities[identity] == database_id,
                       "RELATION_ID_COLLISION")
            identities[identity] = database_id
        if target_db_id is None:
            metrics["missing_target"] += 1
            continue
        v1.require(target_id == requested_id and target_id != supplier_id, "INVALID_RELATION_TARGET")
        pointer = v1.string(edge.get("source_pointer"), 2048)
        v1.require(pointer.startswith("/") and not any(ord(c) < 32 for c in pointer), "INVALID_RELATION_POINTER")
        number = v1.string(edge.get("part_number"), 300)
        v1.require(number == part_number, "RELATION_PART_NUMBER_MISMATCH")
        position_id = edge.get("position_id")
        v1.require(type(position_id) is int and position_id > 0, "INVALID_RELATION_POSITION")
        valid = []
        for ref in evidence or []:
            sha = ref.get("sha256") if isinstance(ref, dict) else None
            if (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
                    and ref.get("json_pointer") == pointer and ref.get("repository_path") == "zip/data/positions.json"):
                valid.append(ref)
        if not valid:
            metrics["missing_evidence"] += 1
            continue
        v1.require(all(ref == valid[0] for ref in valid), "RELATION_EVIDENCE_COLLISION")
        source_url = valid[0].get("url")
        if source_url:
            parsed = parse.urlsplit(v1.string(source_url, 4096))
            v1.require(parsed.scheme in ("http", "https") and bool(parsed.hostname) and
                       parsed.username is None and parsed.password is None and
                       not any(ord(c) <= 32 or ord(c) == 127 for c in source_url), "INVALID_RELATION_SOURCE_URL")
        relation = {"article_id": supplier_id, "name": v1.string(name, 300),
                    "relation_type": "historical_supplier_candidate", "position_id": position_id,
                    "part_number": number, "json_pointer": pointer, "source_sha256": valid[0]["sha256"],
                    "source_url": source_url}
        # Duplicate observations remain in their canonical supplier source; the
        # derived navigation omits only byte-identical edges.
        identity = (target_id, encode(relation))
        if identity in seen:
            metrics["duplicate_edges"] += 1
            continue
        seen.add(identity)
        result[target_id].append(relation)
        v1.require(len(result[target_id]) <= MAX_COMPONENT_RELATIONS, "COMPONENT_RELATION_LIMIT")
        byte_count += len(identity[1]) + len(target_id.encode("utf-8"))
        v1.require(byte_count <= MAX_RELATION_BYTES, "RELATION_BYTE_LIMIT")
        metrics["linked"] += 1
    for links in result.values():
        links.sort(key=lambda r: (r["article_id"], r["position_id"], r["json_pointer"]))
    return dict(result), metrics


class SourceRows:
    def __init__(self, rows, relations, metrics):
        self.rows, self.relations, self.relation_metrics = rows, relations, metrics

    def __iter__(self):
        return iter(self.rows)


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
        for key in ("component_fields", "supplier_fields", "price_fields", "library_relations", "typedfields", "references",
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
    if kind(row) == "component":
        fields = row["sources"].get("component_fields", {})
        if isinstance(fields, dict):
            label = component_type_label(row["sources"], row["segment_id"])
            if label:
                strings.append(label)
                alias = label.replace("ё", "е").replace("Ё", "Е")
                if alias != label:
                    strings.append(alias)
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


KV_KEY_KINDS = frozenset(("cas_blob", "current_v2", "revision_v2", "current_v1", "history_v1", "draft", "other"))
MAX_WRITE_ELAPSED_MS = 300_000


def key_kind(key):
    if not isinstance(key, str):
        return "other"
    if key == CURRENT:
        return "current_v2"
    if key == v1.CURRENT_KEY:
        return "current_v1"
    for prefix, kind in ((PREFIX, "cas_blob"), ("library:v2:revision:", "revision_v2"),
                         ("library:history:", "history_v1"), (v1.DRAFT_PREFIX, "draft")):
        if key.startswith(prefix):
            return kind
    return "other"


def value_fingerprint(raw):
    v1.require(raw is None or isinstance(raw, bytes), "INVALID_READBACK_VALUE")
    return {"presence": "missing" if raw is None else "value",
            "bytes": None if raw is None else len(raw),
            "sha256": None if raw is None else digest(raw)}


class KVReadbackError(v1.PublishError):
    """No raw key, namespace or value crosses the diagnostics boundary."""
    def __init__(self, kind, expected, observations, write_context=None):
        super().__init__("KV_READBACK_NOT_CONFIRMED")
        v1.require(isinstance(kind, str) and kind in KV_KEY_KINDS, "INVALID_READBACK_KIND")
        def checked(value):
            v1.require(isinstance(value, dict) and set(value) == {"presence", "bytes", "sha256"}, "INVALID_READBACK_PROOF")
            missing = value["presence"] == "missing" and value["bytes"] is None and value["sha256"] is None
            present = (value["presence"] == "value" and type(value["bytes"]) is int and
                       0 <= value["bytes"] <= MAX_VALUE_BYTES and isinstance(value["sha256"], str) and
                       re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)
            v1.require(missing or present, "INVALID_READBACK_PROOF")
            return dict(value)
        v1.require(isinstance(observations, list) and len(observations) == len(v1.READBACK_DELAYS), "INVALID_READBACK_PROOF")
        attempts = []
        for number, value in enumerate(observations, 1):
            v1.require(isinstance(value, dict) and set(value) == {"attempt", "presence", "bytes", "sha256"} and
                       type(value["attempt"]) is int and value["attempt"] == number, "INVALID_READBACK_PROOF")
            attempts.append({"attempt": number, **checked({k: v for k, v in value.items() if k != "attempt"})})
        context = {"outcome": "not_observed", "elapsed_ms": None} if write_context is None else write_context
        v1.require(isinstance(context, dict), "INVALID_WRITE_CONTEXT")
        fields = {"outcome", "elapsed_ms"}
        if context.get("outcome") == "transport_uncertain":
            fields.update(("reason", "http_status"))
        v1.require(set(context) == fields, "INVALID_WRITE_CONTEXT")
        outcome, elapsed = context["outcome"], context["elapsed_ms"]
        v1.require((outcome == "not_observed" and elapsed is None) or
                   (outcome in ("acknowledged", "transport_uncertain") and type(elapsed) is int and
                    0 <= elapsed <= MAX_WRITE_ELAPSED_MS), "INVALID_WRITE_CONTEXT")
        if outcome == "transport_uncertain":
            reason, status = context["reason"], context["http_status"]
            v1.require(reason is None or (isinstance(reason, str) and reason in v1.TRANSPORT_REASONS), "INVALID_WRITE_CONTEXT")
            v1.require((reason == "http_error" and type(status) is int and status in v1.TRANSPORT_HTTP_STATUSES) or
                       (reason != "http_error" and status is None), "INVALID_WRITE_CONTEXT")
        self.proof = {"key_kind": kind, "expected": checked(expected), "attempts": attempts,
                      "write_context": dict(context)}


class Cloudflare(v1.Cloudflare):
    def put(self, namespace, key, raw):
        # A context belongs to one exact PUT and must not survive a different or
        # rejected write attempt. Raw identifiers stay only in private memory.
        self._last_write = None
        path = self.value_path(namespace, key)
        v1.require(isinstance(raw, bytes) and len(raw) <= v1.MAX_BYTES, "LIBRARY_TOO_LARGE")
        expected = value_fingerprint(raw)
        started = time.monotonic()
        def record(outcome, failure=None):
            elapsed = min(MAX_WRITE_ELAPSED_MS, max(0, int((time.monotonic() - started) * 1000)))
            self._last_write = {"namespace": namespace, "key": key, "expected": expected,
                                "outcome": outcome, "elapsed_ms": elapsed}
            if outcome == "transport_uncertain":
                reason, status = None, None
                try:
                    candidate = getattr(failure, "transport_reason", None)
                    code = getattr(failure, "transport_http_status", None)
                    if isinstance(candidate, str) and candidate in v1.TRANSPORT_REASONS:
                        if candidate == "http_error":
                            if type(code) is int and code in v1.TRANSPORT_HTTP_STATUSES:
                                reason, status = candidate, code
                        else:
                            reason = candidate
                except Exception:
                    pass
                self._last_write.update(reason=reason, http_status=status)
        try:
            self.envelope("PUT", path, raw)
        except v1.PublishError as failure:
            if str(failure) != "CLOUDFLARE_WRITE_OUTCOME_UNCONFIRMED":
                raise
            record("transport_uncertain", failure)
            # Inherited transport timeout and single-PUT policy stay unchanged.
            self.verify(namespace, key, raw)
        else:
            record("acknowledged")

    def write_context(self, namespace, key, expected):
        context = getattr(self, "_last_write", None)
        if (isinstance(context, dict) and context.get("namespace") == namespace and
                context.get("key") == key and context.get("expected") == expected):
            result = {"outcome": context["outcome"], "elapsed_ms": context["elapsed_ms"]}
            if context["outcome"] == "transport_uncertain":
                result.update(reason=context["reason"], http_status=context["http_status"])
            return result
        return {"outcome": "not_observed", "elapsed_ms": None}

    def verify(self, namespace, key, expected):
        expected_proof = value_fingerprint(expected)
        observations = []
        for attempt, delay in enumerate(v1.READBACK_DELAYS, 1):
            if delay:
                self.sleep(delay)
            observed = self.get(namespace, key)
            if observed == expected:
                return
            observations.append({"attempt": attempt, **value_fingerprint(observed)})
        raise KVReadbackError(key_kind(key), expected_proof, observations,
                              self.write_context(namespace, key, expected_proof))

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


def audit_current(cf):
    """Readonly audit of the stable pointer and its manifest, not every blob."""
    namespace = cf.namespace()
    raw = cf.get(namespace, CURRENT)
    if raw is not None:
        pointer = v1.decode(raw)
        v1.require(isinstance(pointer, dict) and pointer.get("version") == 2, "INVALID_VERSION")
        revision = v1.stable_id(pointer.get("revision"))
        reference = pointer.get("manifest")
        v1.require(isinstance(reference, dict) and type(reference.get("bytes")) is int and
                   0 < reference["bytes"] <= MAX_VALUE_BYTES and isinstance(reference.get("sha256"), str) and
                   re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is not None, "INVALID_BLOB_REFERENCE")
        manifest = Store(cf, namespace).get(reference)
        v1.require(isinstance(manifest, dict) and manifest.get("version") == 2, "INVALID_VERSION")
        v1.require(manifest.get("revision") == revision, "REVISION_MISMATCH")
        count, segments = manifest.get("article_count"), manifest.get("segments")
        v1.require(type(count) is int and 0 <= count <= MAX_ARTICLES, "INVALID_MANIFEST_COUNTS")
        v1.require(isinstance(segments, list) and len(segments) <= MAX_SEGMENTS, "INVALID_MANIFEST_COUNTS")
        total, identities = 0, set()
        for segment in segments:
            v1.require(isinstance(segment, dict), "INVALID_MANIFEST_COUNTS")
            identity = v1.stable_id(segment.get("id"))
            v1.require(identity not in identities, "DUPLICATE_SEGMENT_ID")
            identities.add(identity)
            amount, kinds = segment.get("article_count"), segment.get("counts_by_kind")
            v1.require(type(amount) is int and 0 <= amount <= MAX_ARTICLES and isinstance(kinds, dict),
                       "INVALID_MANIFEST_COUNTS")
            v1.require(set(kinds) == set(KINDS) and
                       all(type(n) is int and 0 <= n <= MAX_ARTICLES for n in kinds.values()) and
                       sum(kinds.values()) == amount, "INVALID_MANIFEST_COUNTS")
            total += amount
        v1.require(total == count, "INVALID_MANIFEST_COUNTS")
        v1.require(cf.get(namespace, CURRENT) == raw, "CURRENT_LIBRARY_CHANGED")
        return {"ok": True, "version": 2, "present": True, "articles": count,
                "segments": len(segments), "scope": "pointer_and_manifest_only"}
    legacy_raw = cf.get(namespace, v1.CURRENT_KEY)
    legacy = v1.snapshot(legacy_raw)
    v1.require(cf.get(namespace, v1.CURRENT_KEY) == legacy_raw, "LEGACY_LIBRARY_CHANGED")
    v1.require(cf.get(namespace, CURRENT) is None, "CURRENT_LIBRARY_CHANGED")
    return {"ok": True, "version": 1 if legacy_raw is not None else None,
            "present": legacy_raw is not None, "articles": len(legacy["articles"]),
            "segments": len(legacy["segments"]), "scope": "pointer_and_manifest_only"}


def blob_audit_spec(env):
    sha, size = env.get("LIBRARY_AUDIT_BLOB_SHA256"), env.get("LIBRARY_AUDIT_BLOB_BYTES")
    if sha is None and size is None:
        return None
    v1.require(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha) is not None and
               isinstance(size, str) and re.fullmatch(r"[1-9][0-9]{0,6}", size) is not None,
               "INVALID_BLOB_AUDIT_ARGUMENTS")
    size = int(size)
    v1.require(size <= MAX_VALUE_BYTES, "INVALID_BLOB_AUDIT_ARGUMENTS")
    return {"sha256": sha, "bytes": size}


def audit_blob(cf, reference):
    """One optional diagnostic GET; a missing blob does not invalidate current."""
    namespace = cf.namespace()
    raw = cf.get(namespace, PREFIX + reference["sha256"])
    expected = {"presence": "value", "bytes": reference["bytes"], "sha256": reference["sha256"]}
    observed = value_fingerprint(raw)
    return {"expected": expected, "observed": observed, "match": observed == expected}


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
    def __init__(self, store, segments, relations=None):
        self.store = store
        self.segments = {v1.stable_id(x["id"]): copy.deepcopy(x) for x in segments}
        v1.require(len(self.segments) == len(segments) <= MAX_SEGMENTS, "INVALID_SEGMENTS")
        self.buffers, self.sizes, self.leaves = {}, {}, defaultdict(list)
        self.buffer_bytes = 0
        self.counts = defaultdict(lambda: defaultdict(int))
        self.confidence_counts = defaultdict(lambda: defaultdict(int))
        self.count, self.last_id = 0, None
        self.body_rows, self.body_bytes = [], 0
        self.relations = relations or {}
        self.relation_targets_seen, self.relation_suppliers_seen = set(), set()
        self.relation_suppliers = {r["article_id"] for links in self.relations.values() for r in links}

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
        if row["id"] in self.relation_suppliers:
            v1.require(kind(normalized) == "supplier" and v1.managed(row), "INVALID_RELATION_SUPPLIER")
            self.relation_suppliers_seen.add(row["id"])
        if row["id"] in self.relations:
            v1.require(kind(normalized) == "component" and v1.managed(row), "INVALID_RELATION_COMPONENT")
            v1.require("library_relations" not in row["sources"], "DERIVED_RELATION_FIELD_CONFLICT")
            # A publication-only projection. No original nested dictionary or
            # database source is mutated; draft readback happens before add().
            row = {**row, "sources": {**row["sources"], "library_relations": {
                "version": 1, "producer": "publisher-v2",
                "candidate_suppliers": self.relations[row["id"]]}}}
            normalized = v1.article(row)
            self.relation_targets_seen.add(row["id"])
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
        v1.require(self.relation_targets_seen == set(self.relations) and
                   self.relation_suppliers_seen == self.relation_suppliers, "RELATION_TARGET_NOT_PUBLISHED")
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


DATABASE_STAGES = frozenset(("connect", "segments_execute", "segments_fetch",
    "relations_execute", "relations_fetch", "articles_execute", "articles_fetch", "close"))
DATABASE_SQLSTATES = frozenset(("08000", "08001", "08003", "08004", "08006", "08007", "08P01",
    "22023", "25006", "25P02", "28000", "28P01", "40001", "40P01", "42501", "42601",
    "42703", "42804", "42883", "42P01", "53000", "53100", "53200", "53300", "53400",
    "54000", "54001", "54011", "55000", "55P03", "57014", "57P01", "57P02", "57P03"))


class DatabaseReadError(v1.PublishError):
    """Allowlisted diagnostic metadata only; never an exception message or DSN."""
    def __init__(self, stage, failure):
        super().__init__("DATABASE_READ_FAILED")
        self.stage = stage if isinstance(stage, str) and stage in DATABASE_STAGES else None
        self.sqlstate = None
        for attribute in ("sqlstate", "pgcode"):
            try:
                value = getattr(failure, attribute, None)
            except Exception:
                continue
            if isinstance(value, str) and value in DATABASE_SQLSTATES:
                self.sqlstate = value
                break


def database_call(stage, operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except v1.PublishError:
        raise
    except Exception as failure:
        raise DatabaseReadError(stage, failure) from None


class Database(v1.Database):
    def connect(self, readonly=True):
        connection = self.driver.connect(**self.parameters)
        try:
            connection.set_session(readonly=readonly, autocommit=False, isolation_level="REPEATABLE READ")
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
            raise
        return connection

    @contextmanager
    def _session(self):
        connection = database_call("connect", self.connect)
        try:
            yield connection
        finally:
            active_error = sys.exc_info()[0] is not None
            cleanup_error = None
            for operation in (connection.rollback, connection.close):
                try:
                    database_call("close", operation)
                except Exception as failure:
                    if cleanup_error is None:
                        cleanup_error = failure
            if cleanup_error is not None and not active_error:
                raise cleanup_error

    @contextmanager
    def _cursor(self, connection, stage, name=None):
        cursor = database_call(stage, connection.cursor, name=name)
        try:
            yield cursor
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                database_call("close", cursor.close)
            except Exception:
                if not active_error:
                    raise

    def _segments(self, connection):
        with self._cursor(connection, "segments_execute") as cursor:
            database_call("segments_execute", cursor.execute, v1.SEGMENTS_SQL)
            rows = database_call("segments_fetch", cursor.fetchmany, MAX_SEGMENTS + 1)
            v1.require(len(rows) <= MAX_SEGMENTS, "SEGMENT_LIMIT")
            return [{"id": v1.stable_id(r[0]), "name": v1.string(r[1], 300),
                     "note": v1.string(r[2], 10000, True)} for r in rows]

    def read_segments(self):
        # Draft FK validation needs no relation projection or article cursor.
        with self._session() as connection:
            return self._segments(connection)

    @contextmanager
    def stream(self):
        with self._session() as connection:
            segments = self._segments(connection)
            with self._cursor(connection, "relations_execute", name="private_library_relations") as cursor:
                cursor.itersize = 50
                database_call("relations_execute", cursor.execute, RELATIONS_SQL,
                              (v1.MANAGER, MAX_RELATION_EDGES + 1, v1.MANAGER, MAX_RELATION_EDGES + 1))
                def projection():
                    while True:
                        rows = database_call("relations_fetch", cursor.fetchmany, 50)
                        if not rows:
                            return
                        yield from rows
                relations, relation_metrics = supplier_relations(projection())
            # One readonly repeatable-read transaction supplies all source rows
            # and the narrow relation projection. Consumer/KV errors propagate.
            with self._cursor(connection, "articles_execute", name="private_library_v2") as cursor:
                cursor.itersize = 50
                database_call("articles_execute", cursor.execute, ORDERED_SQL, (v1.MANAGER,))
                def source():
                    seen = 0
                    while True:
                        rows = database_call("articles_fetch", cursor.fetchmany, 50)
                        if not rows:
                            return
                        for row in rows:
                            seen += 1
                            v1.require(seen <= MAX_ARTICLES, "ARTICLE_LIMIT")
                            normalized = v1.db_article(row)
                            v1.require(v1.managed(normalized), "UNAPPROVED_DATABASE_ROW")
                            yield normalized
                yield segments, SourceRows(source(), relations, relation_metrics)


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
    segments = db.read_segments()
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
        builder = Builder(store, list(merged_segments.values()), getattr(incoming, "relations", {}))
        relation_metrics = getattr(incoming, "relation_metrics", {})
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
            "supplier_relations": relation_metrics,
            "draft_keys_deferred": metrics["draft_keys_deferred"],
            "next_manual_run_required": bool(metrics["draft_keys_deferred"])}


def main(environ=None):
    env = os.environ if environ is None else environ
    try:
        mode = env.get("LIBRARY_PUBLISH_MODE", "publish")
        v1.require(mode in ("publish", "audit"), "INVALID_PUBLISH_MODE")
        reference = blob_audit_spec(env)
        cf = Cloudflare(env.get("CLOUDFLARE_ACCOUNT_ID"), env.get("CLOUDFLARE_API_TOKEN"))
        current = audit_current(cf)
        if mode == "audit":
            result = {"mode": "audit", **current}
            if reference is not None:
                result["blob_audit"] = audit_blob(cf, reference)
        else:
            print(json.dumps({"event": "prepublish_audit", "audit": current}, ensure_ascii=True), file=sys.stderr)
            if reference is not None:
                print(json.dumps({"event": "blob_audit", "audit": audit_blob(cf, reference)}, ensure_ascii=True), file=sys.stderr)
            result = run(Database(env.get("SUPABASE_DB_URL")), cf)
    except KVReadbackError as failure:
        result = {"ok": False, "error": "KV_READBACK_NOT_CONFIRMED", "readback": failure.proof}
    except DatabaseReadError as failure:
        result = {"ok": False, "error": "DATABASE_READ_FAILED",
                  "stage": failure.stage, "sqlstate": failure.sqlstate}
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
