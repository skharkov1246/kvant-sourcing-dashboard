"""Зонд v49: какие поля отдаёт Битрикс по воронкам сорсинга и реализации и как они заполнены.

ВОПРОС. Владелец (09.10.2026): «для воронки сорсинга и реализации — посмотри, что
отдаёт Битрикс, и какие корректировки и обязательные поля ты бы поставил, чтобы не
терять информацию; какие дополнительные». Ответ строится на замере, а не на
догадке: для каждого поля — тип, обязательность в портале, заполненность всего и
по фазам воронки (по ней видно, на какой стадии поле на деле появляется, — это и
есть кандидат на «обязательное на стадии»).

НАБОРЫ.
  1. Сделки воронки «Пресейл» (по имени) — все. Фазы — «на ком мяч»
     (presale.ball_of): руководитель сорсинга / сорсер / КАМ / выиграна / отказ.
  2. Сделки воронки «Реализация» (0) — открытые и созданные за 365 дней. Фазы —
     треть рабочих стадий по порядку (ранние / средние / поздние), успех, отказ.
  3. Карточки «Запросы поставщикам» (СП-166, воронка 24) за 60 дней. Фазы —
     корзины stages.classify_stage (новый, отправлен, переписка, КП, отказ,
     молчание).
  4. Заказы поставщикам (СП-172) — живые и созданные за 365 дней. Фазы — как у
     сделок: трети рабочих стадий, успех, отказ.
  Плюс: товарные строки сделок пресейла (есть ли номенклатура в сделке).

ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): код поля, подпись, тип, признаки и доли.
Значения полей, названия сделок, компаний и людей не печатаются; у списков —
только число использованных вариантов.

    python scripts/probe_fields.py        # нужен BITRIX_WEBHOOK_URL
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from collections import Counter, defaultdict
from urllib import parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import presale as presale_mod  # noqa: E402
import stages as stages_mod  # noqa: E402

RFQ_DAYS = 60
YEAR_DAYS = 365
BATCH = 50

# пустое значение: так портал отдаёт незаполненное поле разных типов
_EMPTY = (None, "", [], {}, False, "0", 0, "N", "0.00", 0.0)
_MONEY_EMPTY = re.compile(r"^(0(\.0+)?)?\|[A-Z]{3}$")


# ------------------------------------------------------------------ чистые помощники
def заполнено(v) -> bool:
    if isinstance(v, (list, tuple)):
        return any(заполнено(x) for x in v)
    if isinstance(v, dict):
        return bool(v) and any(заполнено(x) for x in v.values())
    if v in _EMPTY:
        return False
    if isinstance(v, str):
        s = v.strip()
        return bool(s) and not _MONEY_EMPTY.match(s) and s not in ("0", "N", "0.00")
    return True


def подпись_поля(meta: dict) -> str:
    for k in ("formLabel", "listLabel", "filterLabel", "title"):
        v = str((meta or {}).get(k) or "").strip()
        if v:
            return v
    return ""


def норм(label: str) -> str:
    """Подпись для поиска дублей: без регистра, знаков и служебных слов."""
    s = re.sub(r"[^a-zа-яё0-9 ]+", " ", (label or "").lower())
    s = re.sub(r"\b(old|старое|старый|new|новое|новый|копия|copy)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def доля(n: int, of: int) -> str:
    return f"{round(n / of * 100)}" if of else "—"


def фаза_по_трети(sort: int, рабочие: list[int]) -> str:
    """ранние / средние / поздние — по месту рабочей стадии в порядке воронки."""
    if not рабочие:
        return "ранние"
    k = sorted(рабочие).index(sort) if sort in рабочие else 0
    third = len(рабочие) / 3
    return "ранние" if k < third else ("средние" if k < 2 * third else "поздние")


def таблица(записи: list[dict], фазы: list[str], поля: dict[str, dict], фаза_записи) -> list[dict]:
    """Строки отчёта: на каждое поле — заполненность всего и по фазам."""
    по_фазе = Counter(фаза_записи(r) for r in записи)
    out = []
    for код, meta in поля.items():
        всего = 0
        f = Counter()
        вариантов = set()
        for r in записи:
            v = r.get(код)
            if заполнено(v):
                всего += 1
                f[фаза_записи(r)] += 1
                if meta.get("type") in ("enumeration", "crm_status", "iblock_element"):
                    for x in (v if isinstance(v, list) else [v]):
                        вариантов.add(str(x))
        out.append({
            "code": код, "label": подпись_поля(meta), "type": str(meta.get("type") or ""),
            "req": bool(meta.get("isRequired")), "ro": bool(meta.get("isReadOnly")),
            "multi": bool(meta.get("isMultiple")),
            "n": всего, "pct": доля(всего, len(записи)),
            "phase": {ф: доля(f[ф], по_фазе[ф]) for ф in фазы},
            "variants": len(вариантов),
        })
    return out


def печать(заголовок: str, записи: list[dict], фазы: list[str], строки: list[dict], по_фазе: Counter) -> None:
    print(f"\n== {заголовок}: записей {len(записи)} ==")
    print("  фазы: " + ", ".join(f"{ф} {по_фазе.get(ф, 0)}" for ф in фазы))
    print("  код | тип | обяз | только чтение | подпись | заполнено % | " + " | ".join(фазы) + " | вариантов")
    for s in sorted(строки, key=lambda x: (x["code"].upper().startswith("UF") or x["code"].startswith("ufCrm"),
                                           -(int(x["pct"]) if x["pct"] != "—" else 0), x["code"])):
        print(f"  {s['code']} | {s['type']}{'[]' if s['multi'] else ''} | {'да' if s['req'] else ''} | "
              f"{'да' if s['ro'] else ''} | {s['label'][:70]} | {s['pct']} | "
              + " | ".join(s["phase"][ф] for ф in фазы) + f" | {s['variants'] or ''}")
    польз = [s for s in строки if s["code"].upper().startswith("UF") or s["code"].startswith("ufCrm")]
    пустые = [s for s in польз if s["n"] == 0]
    редкие = [s for s in польз if 0 < s["n"] and s["pct"] != "—" and int(s["pct"]) < 5]
    print(f"  итог пользовательских полей: {len(польз)}; ни разу не заполнены {len(пустые)}; "
          f"заполнены меньше чем у 5 % {len(редкие)}; обязательных в портале "
          f"{sum(1 for s in строки if s['req'])}")
    группы = defaultdict(list)
    for s in польз:
        if норм(s["label"]):
            группы[норм(s["label"])].append(s)
    дубли = [g for g in группы.values() if len(g) > 1]
    if дубли:
        print("  похожие подписи (кандидаты в дубли): " + "; ".join(
            " / ".join(f"{s['code']} {s['pct']}%" for s in g) + f" «{g[0]['label'][:40]}»" for g in дубли))


# ------------------------------------------------------------------ портал
def поля_сделки(client) -> dict[str, dict]:
    return client.call("crm.deal.fields") or {}


def поля_сп(client, entity: int) -> dict[str, dict]:
    res = client.call("crm.item.fields", {"entityTypeId": entity}) or {}
    return (res.get("fields") if isinstance(res, dict) else None) or {}


def строки_сделок(client, ids: list[str]) -> dict[str, int]:
    """Число товарных строк у сделок: batch по 50 команд crm.deal.productrows.get."""
    out: dict[str, int] = {}
    for i in range(0, len(ids), BATCH):
        часть = ids[i:i + BATCH]
        cmd = {f"d{n}": "crm.deal.productrows.get?" + parse.urlencode({"id": n}) for n in часть}
        res = client.call("batch", {"halt": 0, "cmd": cmd}) or {}
        результаты = res.get("result") or {}
        for n in часть:
            v = результаты.get(f"d{n}") if isinstance(результаты, dict) else None
            out[n] = len(v) if isinstance(v, list) else 0
    return out


def main() -> int:
    from bitrix_client import BitrixClient
    settings = config.Settings.load()
    client = BitrixClient(settings.bitrix_webhook_url)
    today = dt.date.today()
    год = (today - dt.timedelta(days=YEAR_DAYS)).isoformat() + "T00:00:00"
    свежие = (today - dt.timedelta(days=RFQ_DAYS)).isoformat() + "T00:00:00"
    meta = client.deal_stage_meta()
    print(f"зонд v49 · {today} · поля воронок сорсинга и реализации")

    # --- сделки: поля одни на все воронки
    fdeal = поля_сделки(client)
    sel = ["*", "UF_*"]

    # 1. пресейл
    try:
        import people as people_mod
        ps = presale_mod.find_category(people_mod.deal_categories(client))
        if ps:
            deals = client.list_deals_fast(filter={"CATEGORY_ID": int(ps[0])}, select=sel)
            фазы = ["руководитель", "сорсер", "КАМ", "выиграна", "отказ", "не размечена"]
            имя = {"head": "руководитель", "src": "сорсер", "kam": "КАМ", "real": "выиграна",
                   "": "отказ", "?": "не размечена"}

            def фаза_п(d):
                m = meta.get(str(d.get("STAGE_ID") or "")) or {}
                return имя[presale_mod.ball_of(m.get("name", ""), m.get("sem", "P"))]
            печать(f"Сделки «{ps[1]}» (воронка #{ps[0]})", deals, фазы,
                   таблица(deals, фазы, fdeal, фаза_п), Counter(фаза_п(d) for d in deals))
            ids = [str(d["ID"]) for d in deals]
            rows = строки_сделок(client, ids)
            с_строками = [n for n in rows.values() if n]
            print(f"  товарные строки: у {len(с_строками)} из {len(ids)} сделок; строк всего {sum(с_строками)}; "
                  f"медиана на сделку со строками {sorted(с_строками)[len(с_строками) // 2] if с_строками else 0}")
        else:
            print("\nВоронка пресейла по имени не найдена")
    except Exception as e:                                       # noqa: BLE001
        print(f"\n  пресейл: зонд оборвался ({type(e).__name__})")

    # 2. реализация (воронка 0)
    try:
        open0 = client.list_deals_fast(filter={"CATEGORY_ID": 0, "STAGE_SEMANTIC_ID": "P"}, select=sel)
        new0 = client.list_deals_fast(filter={"CATEGORY_ID": 0, ">=DATE_CREATE": год}, select=sel)
        deals0 = list({str(d["ID"]): d for d in open0 + new0}.values())
        рабочие = [m["sort"] for s, m in meta.items() if m.get("cat") == "0" and m.get("sem") == "P"]
        фазы0 = ["ранние", "средние", "поздние", "успех", "отказ"]

        def фаза_0(d):
            m = meta.get(str(d.get("STAGE_ID") or "")) or {}
            sem = m.get("sem") or str(d.get("STAGE_SEMANTIC_ID") or "P").upper()
            if sem == "S":
                return "успех"
            if sem == "F":
                return "отказ"
            return фаза_по_трети(m.get("sort", 0), рабочие)
        печать("Сделки «Реализация» (воронка #0): открытые и созданные за 365 дн", deals0, фазы0,
               таблица(deals0, фазы0, fdeal, фаза_0), Counter(фаза_0(d) for d in deals0))
        print("  стадии воронки 0 по порядку: " + " → ".join(
            f"{m['name']}[{m['sem']}]" for s, m in sorted(meta.items(), key=lambda kv: kv[1].get("sort", 0))
            if m.get("cat") == "0"))
    except Exception as e:                                       # noqa: BLE001
        print(f"\n  реализация: зонд оборвался ({type(e).__name__})")

    # 3. запросы поставщикам СП-166
    try:
        f166 = поля_сп(client, config.SPA_ENTITY_TYPE_ID)
        files = [k for k, m in f166.items() if m.get("type") == "file"]
        rfq = client.list_items(config.SPA_ENTITY_TYPE_ID, filter={"categoryId": config.SPA_CATEGORY_ID,
                                                                   ">=createdTime": свежие},
                                select=["*", *files])
        фазы1 = list(stages_mod.BUCKETS)
        фаза_1 = lambda r: stages_mod.classify_stage(str(r.get("stageId") or ""))  # noqa: E731
        печать(f"Запросы поставщикам (СП-166, воронка 24) за {RFQ_DAYS} дн", rfq, фазы1,
               таблица(rfq, фазы1, f166, фаза_1), Counter(фаза_1(r) for r in rfq))
    except Exception as e:                                       # noqa: BLE001
        print(f"\n  СП-166: зонд оборвался ({type(e).__name__})")

    # 4. заказы поставщикам СП-172
    try:
        f172 = поля_сп(client, 172)
        files = [k for k, m in f172.items() if m.get("type") == "file"]
        orders = client.list_items(172, filter={">=createdTime": год}, select=["*", *files])
        ost: dict[str, dict] = {}
        for cid in sorted({m.group(1) for o in orders
                           if (m := re.match(r"DT172_(\d+):", str(o.get("stageId") or "")))}):
            for i, s in enumerate(client.list_paged("crm.status.list", {
                    "filter": {"ENTITY_ID": f"DYNAMIC_172_STAGE_{cid}"}, "order": {"SORT": "ASC"}})):
                ost[s["STATUS_ID"]] = {"sort": int(s.get("SORT") or i), "sem": str(s.get("SEMANTICS") or "P").upper()}
        рабочие2 = [m["sort"] for m in ost.values() if m["sem"] not in ("S", "F")]
        фазы2 = ["ранние", "средние", "поздние", "успех", "отказ"]

        def фаза_2(o):
            sid = str(o.get("stageId") or "")
            m = ost.get(sid) or {}
            if sid.endswith(":SUCCESS") or m.get("sem") == "S":
                return "успех"
            if sid.endswith(":FAIL") or m.get("sem") == "F":
                return "отказ"
            return фаза_по_трети(m.get("sort", 0), рабочие2)
        печать("Заказы поставщикам (СП-172), созданные за 365 дн", orders, фазы2,
               таблица(orders, фазы2, f172, фаза_2), Counter(фаза_2(o) for o in orders))
    except Exception as e:                                       # noqa: BLE001
        print(f"\n  СП-172: зонд оборвался ({type(e).__name__})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
