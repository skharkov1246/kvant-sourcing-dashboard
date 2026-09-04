"""Синтетические данные Bitrix для тестов и smoke-рендера — без обращения к порталу.

Формы записей повторяют реальные ответы Bitrix (СП-166 и crm.deal.list), поэтому
метрики считаются настоящим модулем metrics.build, а не подделкой словаря.
"""
from __future__ import annotations

import datetime as dt
import random

import period as period_mod
import stages as stages_mod

DEPT_A = {"76", "77", "78", "79"}          # «Отдел поиска поставщиков»
DEPT_B = {"90", "91"}
NAMES = {"76": "Иванов И.", "77": "Петрова А.", "78": "Сидоров С.", "79": "Кузнецов К.",
         "90": "Орлов О.", "91": "Волкова В."}


def _stage_ids() -> list[str]:
    """Реальные stageId СП-166 из карты stages.py, чтобы бакеты раскладывались как в проде."""
    ids = [s for s in getattr(stages_mod, "_SPA_STAGE_BUCKET", {}) if isinstance(s, str)]
    return ids or ["DT166_24:NEW", "DT166_24:PREPARATION", "DT166_24:SUCCESS", "DT166_24:FAIL"]


def make_period(start: str = "2026-05-01", end: str = "2026-07-31") -> period_mod.Period:
    return period_mod.parse_period(f"{start}:{end}", as_of=dt.date.fromisoformat(end))


def make_dataset(n_rfq: int = 240, n_deals: int = 120, seed: int = 7) -> dict:
    """Полный набор входов для metrics.build (детерминированный при том же seed)."""
    rnd = random.Random(seed)
    p = make_period()
    stage_ids = _stage_ids()
    users = sorted(DEPT_A | DEPT_B)
    span = (p.end - p.start).days or 1

    rfqs = []
    for i in range(n_rfq):
        created = p.start + dt.timedelta(days=rnd.randint(0, span))
        moved = created + dt.timedelta(days=rnd.randint(0, 20))
        rfqs.append({
            "id": 1000 + i,
            "assignedById": int(rnd.choice(users)),
            "stageId": rnd.choice(stage_ids),
            "createdTime": created.isoformat() + "T10:00:00+03:00",
            "movedTime": moved.isoformat() + "T10:00:00+03:00",
            "parentId2": 500 + (i % n_deals),
            "categoryId": 24,
            "title": f"Запрос №{i}",
            "companyId": 300 + (i % 40),
            "ufCrm18Supplier": [f"CO_{300 + (i % 40)}"],
            "_supplier": f"ООО Поставщик-{i % 40}",
        })

    deal_stages = ["C24:NEW", "C24:PREPARATION", "C24:WON", "C24:LOSE", "C0:WON"]
    deal_index, period_deals = {}, []
    for j in range(n_deals):
        did = str(500 + j)
        created = p.start + dt.timedelta(days=rnd.randint(0, span))
        d = {
            "ID": did, "TITLE": f"Сделка {j}", "CATEGORY_ID": str(rnd.choice([0, 24, 7])),
            "STAGE_ID": rnd.choice(deal_stages),
            "STAGE_SEMANTIC_ID": rnd.choice(["P", "S", "F"]),
            "DATE_CREATE": created.isoformat() + "T09:00:00+03:00",
            "ASSIGNED_BY_ID": rnd.choice(users),
            "COMPANY_ID": str(300 + (j % 40)),
            "OPPORTUNITY": str(rnd.randint(10_000, 5_000_000)),
            "CURRENCY_ID": "RUB",
        }
        deal_index[did] = d
        period_deals.append(d)

    return {
        "period": p, "rfqs": rfqs, "deal_index": deal_index, "period_deals": period_deals,
        "dept_a_ids": set(DEPT_A), "names": dict(NAMES),
        "since": {u: "2025-01-01" for u in users},
        "deal_stage_names": {s: s.split(":")[-1].title() for s in deal_stages},
        "category_names": {"0": "Продажи", "24": "Сорсинг", "7": "Сервис"},
    }


def build_metrics(**kw) -> dict:
    """Синтетика → настоящий metrics.build → метрики той же формы, что в проде."""
    import metrics as metrics_mod
    d = make_dataset(**kw)
    return metrics_mod.build(d["period"], d["rfqs"], d["deal_index"], d["period_deals"],
                             d["dept_a_ids"], d["names"], d["since"],
                             d["deal_stage_names"], d["category_names"])
