"""Клиент Bitrix24 REST поверх входящего вебхука.

Возможности:
  * устойчивые вызовы с ретраями и обработкой лимита запросов;
  * быстрая постраничная выгрузка больших списков (ID-based fast method);
  * кэш справочников: пользователи, стадии, воронки, enum-поля сделок.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Iterable

import requests


class BitrixError(RuntimeError):
    pass


class BitrixClient:
    def __init__(self, webhook_url: str, *, timeout: int = 30, min_interval: float = 0.34):
        self.base = webhook_url.rstrip("/") + "/"
        self.timeout = timeout
        self.min_interval = min_interval  # ~3 запроса/сек, под лимит Bitrix
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._last_call = 0.0
        # ленивые кэши
        self._users: dict[str, str] | None = None
        self._stages: dict[str, str] | None = None
        self._categories: dict[str, str] | None = None
        self._uf: dict[str, dict] | None = None
        self._departments: list[dict] | None = None

    # ----------------------------------------------------------------- low level
    def call(self, method: str, params: dict | None = None, *, retries: int = 4) -> Any:
        url = self.base + method + ".json"
        payload = params or {}
        last_err: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self._session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as e:  # сеть
                last_err = e
                time.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code == 503 or r.status_code == 429:
                time.sleep(0.7 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                raise BitrixError(f"{method}: не JSON-ответ (HTTP {r.status_code}): {r.text[:200]}")
            if isinstance(data, dict) and data.get("error"):
                err = data.get("error")
                desc = data.get("error_description", "")
                if err in ("QUERY_LIMIT_EXCEEDED", "OPERATION_TIME_LIMIT"):
                    time.sleep(0.7 * (attempt + 1))
                    last_err = BitrixError(f"{method}: {err} {desc}")
                    continue
                raise BitrixError(f"{method}: {err} {desc}")
            return data.get("result") if isinstance(data, dict) else data
        raise BitrixError(f"{method}: не удалось выполнить за {retries} попыток ({last_err})")

    def _throttle(self) -> None:
        with self._lock:
            dt = time.monotonic() - self._last_call
            if dt < self.min_interval:
                time.sleep(self.min_interval - dt)
            self._last_call = time.monotonic()

    # ----------------------------------------------------------------- listing
    def list_paged(self, method: str, params: dict | None = None, *, max_items: int | None = None) -> list[dict]:
        """Классическая постраничная выгрузка (start += 50)."""
        params = dict(params or {})
        out: list[dict] = []
        start = 0
        while True:
            params["start"] = start
            url = self.base + method + ".json"
            self._throttle()
            data = self._session.post(url, json=params, timeout=self.timeout).json()
            if data.get("error"):
                raise BitrixError(f"{method}: {data['error']} {data.get('error_description','')}")
            chunk = data.get("result") or []
            if isinstance(chunk, dict):  # некоторые методы возвращают dict
                chunk = list(chunk.values())
            out.extend(chunk)
            if max_items and len(out) >= max_items:
                return out[:max_items]
            nxt = data.get("next")
            if not nxt:
                break
            start = nxt
        return out

    def list_deals_fast(
        self,
        *,
        filter: dict | None = None,
        select: Iterable[str] | None = None,
        order_field: str = "ID",
        max_items: int | None = None,
    ) -> list[dict]:
        """Быстрая выгрузка сделок методом ID > last_id, start=-1 (без подсчёта total)."""
        select = list(select or ["*", "UF_*"])
        base_filter = dict(filter or {})
        out: list[dict] = []
        last_id = 0
        while True:
            f = dict(base_filter)
            f[">ID"] = last_id
            params = {"order": {"ID": "ASC"}, "filter": f, "select": select, "start": -1}
            chunk = self.call("crm.deal.list", params) or []
            if not chunk:
                break
            out.extend(chunk)
            last_id = int(chunk[-1]["ID"])
            if max_items and len(out) >= max_items:
                return out[:max_items]
            if len(chunk) < 50:
                break
        return out

    def count_deals(self, filter: dict | None = None) -> int:
        data = self._session.post(
            self.base + "crm.deal.list.json",
            json={"filter": filter or {}, "select": ["ID"], "start": 0},
            timeout=self.timeout,
        ).json()
        return int(data.get("total") or 0)

    # ----------------------------------------------------------------- smart-process items
    def list_items(
        self,
        entity_type_id: int,
        *,
        filter: dict | None = None,
        select: Iterable[str] | None = None,
        max_items: int | None = None,
    ) -> list[dict]:
        """Быстрая выгрузка записей смарт-процесса (crm.item.list) методом id > last_id."""
        select = list(select or ["*"])
        base = dict(filter or {})
        out: list[dict] = []
        last = 0
        while True:
            f = dict(base)
            f[">id"] = last
            res = self.call(
                "crm.item.list",
                {"entityTypeId": entity_type_id, "order": {"id": "ASC"}, "filter": f, "select": select, "start": -1},
            )
            items = (res or {}).get("items", []) if isinstance(res, dict) else []
            if not items:
                break
            out.extend(items)
            last = int(items[-1]["id"])
            if max_items and len(out) >= max_items:
                return out[:max_items]
            if len(items) < 50:
                break
        return out

    def spa_stages(self, entity_type_id: int, category_id: int) -> dict[str, str]:
        """{stageId: name} для воронки смарт-процесса."""
        ent = f"DYNAMIC_{entity_type_id}_STAGE_{category_id}"
        m: dict[str, str] = {}
        for s in self.list_paged("crm.status.list", {"filter": {"ENTITY_ID": ent}, "order": {"SORT": "ASC"}}):
            m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
        return m

    # ----------------------------------------------------------------- departments
    def departments(self) -> list[dict]:
        if self._departments is None:
            self._departments = self.list_paged("department.get", {})
        return self._departments

    def dept_member_ids(self, dept_id: int | str, *, include_children: bool = True) -> set[str]:
        """ID пользователей отдела (по UF_DEPARTMENT), включая дочерние отделы."""
        deps = self.departments()
        ids = {str(dept_id)}
        if include_children:
            changed = True
            while changed:
                changed = False
                for d in deps:
                    if str(d.get("PARENT")) in ids and str(d["ID"]) not in ids:
                        ids.add(str(d["ID"]))
                        changed = True
        members: set[str] = set()
        for did in ids:
            for u in self.list_paged("user.get", {"FILTER": {"UF_DEPARTMENT": int(did)}}):
                members.add(str(u["ID"]))
        return members

    # ----------------------------------------------------------------- deals (для покрытия и цепочки ТКП)
    def deals_in_period(self, date_from: str, date_to: str, *, select: Iterable[str] | None = None) -> list[dict]:
        select = list(select or ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "DATE_CREATE", "ASSIGNED_BY_ID"])
        return self.list_deals_fast(
            filter={">=DATE_CREATE": date_from, "<=DATE_CREATE": date_to}, select=select
        )

    def deals_by_ids(self, ids: Iterable, *, select: Iterable[str] | None = None, chunk: int = 50) -> dict[str, dict]:
        select = list(select or ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID"])
        clean = [int(i) for i in ids if i]
        out: dict[str, dict] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.deal.list", {"filter": {"@ID": part}, "select": select, "start": -1}) or []
            for d in res:
                out[str(d["ID"])] = d
        return out

    def companies_by_ids(self, ids: Iterable, *, chunk: int = 50) -> dict[str, str]:
        clean = sorted({int(i) for i in ids if str(i).isdigit()})
        out: dict[str, str] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.company.list", {"filter": {"@ID": part}, "select": ["ID", "TITLE"], "start": -1}) or []
            for c in res:
                out[str(c["ID"])] = c.get("TITLE") or f"company#{c['ID']}"
        return out

    def contacts_by_ids(self, ids: Iterable, *, chunk: int = 50) -> dict[str, str]:
        clean = sorted({int(i) for i in ids if str(i).isdigit()})
        out: dict[str, str] = {}
        for i in range(0, len(clean), chunk):
            part = clean[i : i + chunk]
            res = self.call("crm.contact.list", {"filter": {"@ID": part}, "select": ["ID", "NAME", "LAST_NAME"], "start": -1}) or []
            for c in res:
                nm = " ".join(x for x in [c.get("NAME"), c.get("LAST_NAME")] if x).strip()
                out[str(c["ID"])] = nm or f"contact#{c['ID']}"
        return out

    # ----------------------------------------------------------------- per-deal data
    def get_comments(self, deal_id: int | str) -> list[dict]:
        return self.list_paged(
            "crm.timeline.comment.list",
            {"filter": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal"}, "order": {"CREATED": "ASC"}},
        )

    def get_activities(self, deal_id: int | str) -> list[dict]:
        return self.list_paged(
            "crm.activity.list",
            {
                "filter": {"OWNER_TYPE_ID": 2, "OWNER_ID": int(deal_id)},
                "order": {"CREATED": "ASC"},
                "select": ["ID", "SUBJECT", "TYPE_ID", "DESCRIPTION", "DIRECTION", "COMPLETED", "CREATED"],
            },
        )

    def get_product_rows(self, deal_id: int | str) -> list[dict]:
        return self.call("crm.deal.productrows.get", {"id": int(deal_id)}) or []

    # ----------------------------------------------------------------- reference maps (cached)
    def users(self) -> dict[str, str]:
        if self._users is None:
            m: dict[str, str] = {}
            rows = self.list_paged("user.get", {})
            for u in rows:
                uid = str(u.get("ID"))
                name = " ".join(x for x in [u.get("NAME"), u.get("LAST_NAME")] if x).strip() or f"user#{uid}"
                pos = u.get("WORK_POSITION")
                m[uid] = f"{name} ({pos})" if pos else name
            self._users = m
        return self._users

    def user_name(self, uid: Any) -> str:
        if uid in (None, "", 0, "0"):
            return ""
        return self.users().get(str(uid), f"user#{uid}")

    def categories(self) -> dict[str, str]:
        if self._categories is None:
            m = {"0": "Общая"}
            for c in self.call("crm.dealcategory.list", {"select": ["ID", "NAME"]}) or []:
                m[str(c["ID"])] = c.get("NAME") or f"cat#{c['ID']}"
            self._categories = m
        return self._categories

    def stages(self) -> dict[str, str]:
        if self._stages is None:
            m: dict[str, str] = {}
            for s in self.list_paged("crm.status.list", {"order": {"SORT": "ASC"}}):
                if str(s.get("ENTITY_ID", "")).startswith("DEAL_STAGE"):
                    m[s["STATUS_ID"]] = s.get("NAME") or s["STATUS_ID"]
            self._stages = m
        return self._stages

    def userfields(self) -> dict[str, dict]:
        """{FIELD_NAME: {"type": USER_TYPE_ID, "enum": {item_id: value}}}"""
        if self._uf is None:
            m: dict[str, dict] = {}
            for f in self.list_paged("crm.deal.userfield.list", {"order": {"ID": "ASC"}}):
                name = f.get("FIELD_NAME")
                if not name:
                    continue
                enum = {str(i["ID"]): i.get("VALUE") for i in (f.get("LIST") or [])}
                m[name] = {"type": f.get("USER_TYPE_ID"), "enum": enum}
            self._uf = m
        return self._uf

    def warm_reference_caches(self) -> None:
        self.users(); self.categories(); self.stages(); self.userfields()
