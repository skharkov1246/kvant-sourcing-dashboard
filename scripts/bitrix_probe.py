"""Зонд v36: чем атрибутировать сделку человеку — полем «КАМ»/«Product leader» или отделом.

v35 показал, что у сделки есть служебные поля с людьми: «КАМ» (UF_CRM_1740390857),
«Product leader» (UF_CRM_1779187425), «Руководитель продуктового направления»
(UF_CRM_1789481932), «Ответственный за реализацию» (UF_CRM_1781858627), «Сорсер».
Если они заполнены — роль сотрудника берётся из карточки, а не угадывается по отделу.
Здесь меряется их заполняемость, расхождение с ответственным и доля уволенных в них.

Ещё проверяется, годится ли CLOSEDATE под «просрочку» (в v35 у ВСЕХ сделок дата стоит,
то есть она проставляется автоматом) и есть ли у сделки MOVED_TIME/LAST_ACTIVITY_TIME
под «застой».

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ. Самое важное — в конце (журнал читается с хвоста).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bitrix_client import BitrixClient  # noqa: E402

KAM_F = "UF_CRM_1740390857"          # «КАМ»
KAM_OLD = "UF_CRM_1736926032"        # «KAM (старое)»
PROD_F = "UF_CRM_1779187425"         # «Product leader»
PROD_OLD = "UF_CRM_1776169499"       # «Product leader (заменить на множ)»
PROD_HEAD = "UF_CRM_1789481932"      # «Руководитель продуктового направления»
REAL_F = "UF_CRM_1781858627"         # «Ответственный за реализацию»
SOURCER = "UF_CRM_1779187335"        # «Сорсер»
OSS_DEAL = "UF_CRM_1715604558"       # «Сопровождение сделки»
PEOPLE_FIELDS = [("КАМ", KAM_F), ("KAM старое", KAM_OLD), ("Product leader", PROD_F),
                 ("Product leader старое", PROD_OLD), ("Рук. продуктового напр.", PROD_HEAD),
                 ("Ответственный за реализацию", REAL_F), ("Сорсер", SOURCER),
                 ("Сопровождение сделки", OSS_DEAL)]
DL_CUSTOMER = "ufCrm20_1728900218435"


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def uid_of(v):
    """Значение employee-поля → id пользователя (строка) или ''."""
    if isinstance(v, list):
        v = v[0] if v else None
    s = str(v or "").strip()
    return s if s.isdigit() and s != "0" else ""


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])
    today = dt.date.today().isoformat()

    # ---------------------------------------------------------------- состав
    users = c.list_paged("user.get", {"ADMIN_MODE": True})
    act, fired, pos = set(), set(), {}
    udept = {}
    for u in users:
        uid = str(u["ID"])
        (act if str(u.get("ACTIVE")).lower() in ("y", "true", "1") else fired).add(uid)
        pos[uid] = (u.get("WORK_POSITION") or "").strip()
        dd = u.get("UF_DEPARTMENT") or []
        udept[uid] = [str(x) for x in (dd if isinstance(dd, list) else [dd])]

    head("1. ДЕРЕВО ОТДЕЛОВ (id · родитель · активных · уволенных · название)")
    deps = c.list_paged("department.get", {})
    dname = {str(d["ID"]): d.get("NAME", "") for d in deps}
    dpar = {str(d["ID"]): str(d.get("PARENT") or "") for d in deps}
    a_cnt, f_cnt = Counter(), Counter()
    for uid in act:
        for d in udept.get(uid) or []:
            a_cnt[d] += 1
    for uid in fired:
        for d in udept.get(uid) or []:
            f_cnt[d] += 1
    def chain(d):
        out, cur, guard = [], d, 0
        while cur and guard < 10:
            out.append(cur); cur = dpar.get(cur, ""); guard += 1
        return "→".join(reversed(out))
    for d in sorted(dname, key=lambda x: int(x)):
        print(f"dept {d:>4} род.{dpar.get(d,'—'):>4}  акт.{a_cnt[d]:>3} увол.{f_cnt[d]:>3}  путь {chain(d):<20} {dname[d]}")

    head("2. ДОЛЖНОСТИ активных — топ-30")
    for p, n in Counter(pos[u] or "(пусто)" for u in act).most_common(30):
        print(f"{n:>4}  {p}")
    print(f"\nвсего пользователей {len(users)} · активных {len(act)} · уволенных {len(fired)}")

    # ---------------------------------------------------------------- сделки
    sel = ["ID", "CATEGORY_ID", "STAGE_ID", "STAGE_SEMANTIC_ID", "OPPORTUNITY", "CURRENCY_ID",
           "DATE_CREATE", "DATE_MODIFY", "CLOSEDATE", "ASSIGNED_BY_ID", "MOVED_TIME",
           "LAST_ACTIVITY_TIME", "BEGINDATE"] + [f for _, f in PEOPLE_FIELDS]
    op = c.list_deals_fast(filter={"STAGE_SEMANTIC_ID": "P"}, select=sel)
    print(f"\nоткрытых сделок: {len(op)}")

    head("3. СИСТЕМНЫЕ ДАТЫ: годятся ли под «застой» и «просрочку»")
    have = lambda f: sum(1 for d in op if str(d.get(f) or "").strip())
    for f in ("MOVED_TIME", "LAST_ACTIVITY_TIME", "DATE_MODIFY", "CLOSEDATE", "BEGINDATE"):
        print(f"{f:<20} заполнено {have(f):>5} из {len(op)}")
    def older(field, days):
        cut = (dt.datetime.now() - dt.timedelta(days=days)).isoformat()[:10]
        return sum(1 for d in op if str(d.get(field) or "")[:10] and str(d[field])[:10] < cut)
    for f in ("MOVED_TIME", "LAST_ACTIVITY_TIME", "DATE_MODIFY"):
        print(f"{f:<20} старше: 14дн {older(f,14):>5} · 30дн {older(f,30):>5} · 60дн {older(f,60):>5} "
              f"· 90дн {older(f,90):>5} · 180дн {older(f,180):>5}")
    cl = Counter()
    for d in op:
        s = str(d.get("CLOSEDATE") or "")[:10]
        if not s:
            cl["(пусто)"] += 1
        elif s < today:
            cl["в прошлом"] += 1
        elif s[:7] == today[:7]:
            cl["этот месяц"] += 1
        else:
            cl["в будущем"] += 1
    print("CLOSEDATE у открытых: " + " · ".join(f"{k} {v}" for k, v in cl.most_common()))
    same = sum(1 for d in op if str(d.get("CLOSEDATE") or "")[:10] == str(d.get("DATE_CREATE") or "")[:10])
    print(f"CLOSEDATE == дата создания (значит, дефолт, а не план): {same} из {len(op)}")

    head("4. ЗАКАЗЫ СП-172: дедлайн клиенту как источник просрочки")
    try:
        orders = c.list_items(172, filter={">=createdTime": "2025-01-01T00:00:00"},
                              select=["id", "stageId", "opportunity", "currencyId", "parentId2",
                                      "assignedById", DL_CUSTOMER])
        live = [o for o in orders if not str(o.get("stageId", "")).endswith((":SUCCESS", ":FAIL"))]
        wdl = [o for o in live if str(o.get(DL_CUSTOMER) or "")[:10]]
        late = [o for o in wdl if str(o[DL_CUSTOMER])[:10] < today]
        print(f"заказов с 2025: {len(orders)} · живых (не SUCCESS/FAIL): {len(live)}")
        print(f"из живых с дедлайном клиенту: {len(wdl)} · просрочено: {len(late)}")
    except Exception as e:
        print(f"СП-172: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- поля-люди
    head("5. ПОЛЯ-ЛЮДИ НА СДЕЛКЕ: заполняемость по открытым сделкам")
    for label, f in PEOPLE_FIELDS:
        vals = [uid_of(d.get(f)) for d in op]
        filled = [v for v in vals if v]
        dif = sum(1 for d, v in zip(op, vals) if v and v != str(d.get("ASSIGNED_BY_ID")))
        firedn = sum(1 for v in filled if v in fired)
        print(f"{label:<28} {f:<22} заполнено {len(filled):>5}/{len(op)} ({len(filled)*100//max(1,len(op)):>3}%)"
              f" · людей {len(set(filled)):>3} · уволенных в поле {firedn:>4} · отличается от ответственного {dif:>5}")

    head("6. ЗАПОЛНЯЕМОСТЬ «КАМ» И «PRODUCT LEADER» ПО ВОРОНКАМ (открытые)")
    cats = {}
    try:
        r = c.call("crm.category.list", {"entityTypeId": 2}) or {}
        for x in (r.get("categories") if isinstance(r, dict) else r) or []:
            cats[str(x.get("id"))] = x.get("name")
    except Exception:
        pass
    by = defaultdict(lambda: [0, 0, 0, 0])   # всего, КАМ, product leader, оба пустые
    for d in op:
        b = by[str(d.get("CATEGORY_ID") or "0")]
        b[0] += 1
        k, p = uid_of(d.get(KAM_F)), uid_of(d.get(PROD_F))
        b[1] += bool(k); b[2] += bool(p); b[3] += (not k and not p)
    for cid, (n, k, p, z) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        print(f"cat {cid:>3} {str(cats.get(cid,''))[:28]:<28} всего {n:>5} · КАМ {k:>5} · prod.leader {p:>5} · оба пустые {z:>5}")

    head("7. ИТОГ: чем атрибутировать — полем или ответственным")
    own = Counter(str(d.get("ASSIGNED_BY_ID") or "") for d in op)
    print(f"ответственный (ASSIGNED_BY_ID) заполнен у {sum(own.values())} из {len(op)}; людей {len(own)}; "
          f"уволенных среди них {len([u for u in own if u in fired])} "
          f"(их сделок {sum(n for u, n in own.items() if u in fired)})")
    kam_u = Counter(uid_of(d.get(KAM_F)) for d in op if uid_of(d.get(KAM_F)))
    prod_u = Counter(uid_of(d.get(PROD_F)) for d in op if uid_of(d.get(PROD_F)))
    print(f"поле «КАМ»: сделок {sum(kam_u.values())}, людей {len(kam_u)}, из них уволенных {len([u for u in kam_u if u in fired])}")
    print(f"поле «Product leader»: сделок {sum(prod_u.values())}, людей {len(prod_u)}, из них уволенных {len([u for u in prod_u if u in fired])}")
    print("должности тех, кто стоит в поле «КАМ» (агрегат): "
          + " · ".join(f"{p or '(пусто)'}×{n}" for p, n in Counter(pos.get(u, "") for u in kam_u).most_common(12)))
    print("должности тех, кто стоит в поле «Product leader»: "
          + " · ".join(f"{p or '(пусто)'}×{n}" for p, n in Counter(pos.get(u, "") for u in prod_u).most_common(12)))
    print("отделы владельцев открытых сделок (топ-12): "
          + " · ".join(f"{d}:{n}" for d, n in Counter(
              dd for u, k in own.items() for dd in (udept.get(u) or ["—"]) for _ in range(k)).most_common(12)))
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
