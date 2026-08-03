"""Эмулятор портала Bitrix24 для смоков коннектора (docs/09 §9.5).

Реализует ровно те три метода REST, которыми пользуется BitrixConnector:
crm.item.list / crm.item.update / crm.timeline.comment.add — с форматом ответов
как у настоящего портала ({"result": ...}). Элементы смарт-процесса живут в
памяти; сидируются сделками стадии NEED_SCAN.

Назначение: доказать коннектор от опроса до обратной записи БЕЗ реального
портала — до появления вебхука владельца. Это не мок в тесте, а HTTP-форма:
тот же эмулятор поднимается рядом с devserver для ручной отладки.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import Body, FastAPI


class BitrixState:
    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}
        self.timeline: list[dict[str, Any]] = []
        self.companies: dict[int, dict[str, Any]] = {}
        self._next_id = 100

    def seed_deal(self, title: str, stage_id: str = "NEED_SCAN",
                  updated: str | None = None) -> dict[str, Any]:
        self._next_id += 1
        item = {
            "id": self._next_id,
            "title": title,
            "stageId": stage_id,
            "companyId": 7,
            "updatedTime": updated or dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.items[item["id"]] = item
        return item

    def seed_company(self, title: str, inn: str | None = None) -> dict[str, Any]:
        self._next_id += 1
        company = {"id": self._next_id, "title": title, "ufInn": inn}
        self.companies[company["id"]] = company
        return company

    def reset(self) -> None:
        self.items.clear()
        self.timeline.clear()
        self.companies.clear()
        self._next_id = 100


def create_emulator(state: BitrixState | None = None) -> tuple[FastAPI, BitrixState]:
    state = state or BitrixState()
    app = FastAPI(title="Bitrix24 emulator")

    @app.post("/rest/crm.item.list")
    def item_list(params: dict[str, Any] = Body(...)) -> dict[str, Any]:
        filter_ = params.get("filter") or {}
        stages = filter_.get("stageId") or []
        since = filter_.get(">=updatedTime")
        items = [
            item for item in state.items.values()
            if (not stages or item["stageId"] in stages)
            and (since is None or item["updatedTime"] >= since)
        ]
        items.sort(key=lambda i: i["updatedTime"])
        return {"result": {"items": items}, "total": len(items)}

    @app.post("/rest/crm.item.update")
    def item_update(params: dict[str, Any] = Body(...)) -> dict[str, Any]:
        item = state.items.get(int(params["id"]))
        if item is None:
            return {"error": "NOT_FOUND", "error_description": "Элемент не найден"}
        item.update(params.get("fields") or {})
        item["updatedTime"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return {"result": {"item": item}}

    @app.post("/rest/crm.company.list")
    def company_list(params: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return {"result": {"items": sorted(state.companies.values(), key=lambda c: c["id"])}}

    @app.post("/rest/crm.timeline.comment.add")
    def timeline_add(params: dict[str, Any] = Body(...)) -> dict[str, Any]:
        fields = params.get("fields") or {}
        entry = {
            "id": len(state.timeline) + 1,
            "entity_id": fields.get("ENTITY_ID"),
            "entity_type": fields.get("ENTITY_TYPE"),
            "comment": fields.get("COMMENT", ""),
        }
        state.timeline.append(entry)
        return {"result": entry["id"]}

    return app, state
