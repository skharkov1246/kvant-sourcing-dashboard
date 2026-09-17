"""Зонд v40: руководители коммерческих ролей — есть ли они в Bitrix и сходятся ли.

Вкладка «Коммерсанты» получает слой руководителей: у КАМов свой начальник, у
продукт-оунеров свой. Прежде чем вводить его в код, надо понять, чем руководитель
определяется объективно: полем отдела UF_HEAD, должностью или ничем.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ: идентификаторы отделов, их названия, должности,
счётчики. Ни фамилий, ни имён — рабочий портал за Cloudflare Access покажет их сам.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import people as people_mod  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402


def head(t: str) -> None:
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main() -> int:
    c = BitrixClient(os.environ["BITRIX_WEBHOOK_URL"])
    ppl = people_mod.roster(c)
    deps_raw = c.list_paged("department.get", {})
    dname = {str(d["ID"]): d.get("NAME", "") for d in deps_raw}
    dpar = {str(d["ID"]): str(d.get("PARENT") or "") for d in deps_raw}
    dhead = {str(d["ID"]): str(d.get("UF_HEAD") or "") for d in deps_raw}
    role_of = people_mod.roles_by_uid(ppl, dname)

    head("1. ДЕРЕВО ОТДЕЛОВ С РУКОВОДИТЕЛЯМИ (id · родитель · рук. · роль рук. · должность рук.)")
    for d in sorted(dname, key=lambda x: int(x)):
        h = dhead.get(d, "")
        hp = ppl.get(h, {})
        print(f"dept {d:>4} род.{dpar.get(d,'—'):>4} рук.{'да ' if h else 'нет'} "
              f"роль:{role_of.get(h, '—'):<6} {('акт' if hp.get('active') else 'увол') if h else '   '} "
              f"{(hp.get('pos') or '')[:42]:<42} {dname[d][:44]}")

    head("2. ОТДЕЛЫ, В КОТОРЫХ СИДЯТ ЛЮДИ РОЛЕЙ, И ИХ РУКОВОДИТЕЛИ")
    for role in ("kam", "prod"):
        team = [u for u, r in role_of.items() if r == role and ppl.get(u, {}).get("active")]
        depts = Counter(d for u in team for d in (ppl[u].get("depts") or []))
        heads = Counter(dhead.get(d, "") for d in depts if dhead.get(d))
        print(f"\nроль {role}: людей {len(team)}, отделов {len(depts)}")
        for d, n in depts.most_common():
            h = dhead.get(d, "")
            print(f"  dept {d:>4} людей {n:>2} рук.{'есть' if h else 'НЕТ '} "
                  f"{'(он же в роли ' + role_of.get(h, '—') + ')' if h else '':<26} {dname.get(d,'')[:40]}")
        print(f"  уникальных руководителей: {len(heads)}; "
              f"самый частый покрывает {heads.most_common(1)[0][1] if heads else 0} отделов из {len(depts)}")
        print("  должности руководителей: " + " · ".join(
            f"{(ppl.get(h, {}).get('pos') or '(пусто)')[:44]}" for h, _ in heads.most_common(6)))

    head("3. КАНДИДАТЫ В РУКОВОДИТЕЛИ ПО ДОЛЖНОСТИ (агрегат, без имён)")
    import re
    boss = re.compile(r"head of|chief|director|руководител|начальник|дирек", re.I)
    cnt = Counter()
    for u, p in ppl.items():
        if p["active"] and boss.search(p.get("pos") or ""):
            cnt[(p.get("pos") or "").strip()[:60]] += 1
    for pos, n in cnt.most_common(40):
        who = [u for u, p in ppl.items() if p["active"] and (p.get("pos") or "").strip()[:60] == pos]
        depts = {d for u in who for d in (ppl[u].get("depts") or [])}
        leads = {d for d in dname if dhead.get(d) in who}
        print(f"{n:>3}  роль:{','.join(sorted({role_of.get(u,'—') for u in who})):<12} "
              f"отделы:{len(depts):>2} возглавляет отделов:{len(leads):>2}  {pos}")

    head("4. ИТОГ: чем определять руководителя роли")
    for role in ("kam", "prod"):
        team = [u for u, r in role_of.items() if r == role and ppl.get(u, {}).get("active")]
        depts = {d for u in team for d in (ppl[u].get("depts") or [])}
        # поднимаемся по дереву: первый предок с руководителем вне самой роли
        tops = Counter()
        for d in depts:
            cur, guard = d, 0
            while cur and guard < 8:
                h = dhead.get(cur, "")
                if h and h not in team:
                    tops[cur] += 1
                    break
                cur = dpar.get(cur, ""); guard += 1
        print(f"роль {role}: отделов {len(depts)}; общий вышестоящий отдел с руководителем "
              f"находится у {sum(tops.values())} из них")
        for d, n in tops.most_common(5):
            h = dhead.get(d, "")
            print(f"   через dept {d:>4} ({n} отделов) → рук. роль:{role_of.get(h,'—')} "
                  f"должность: {(ppl.get(h, {}).get('pos') or '(пусто)')[:50]} · {dname.get(d,'')[:36]}")
    print("\nГОТОВО")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
