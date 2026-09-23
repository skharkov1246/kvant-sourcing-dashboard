"""Зонд v46: кто из сорсеров стоит за каждой карточкой робота запросов.

ЧТО БЫЛО НЕ ТАК В v45. «Аккаунт №2 Служебный» — ИМЯ учётной записи, а не её
номер в портале. Я искал пользователя с идентификатором 2 и честно получал ноль
карточек. Второе: выборка брала первые 1 500 карточек отчётного окна (выгрузка
идёт по возрастанию идентификатора), то есть май — а робот запущен в середине
сентября и даёт всплеск «вне отдела» на неделях W21–W22. Его карточек в той
выборке не было вовсе.

ПОРЯДОК ЗОНДА:
  1. найти служебные записи ПО ИМЕНИ — «служебн», «аккаунт», «робот», «бот»,
     «robot», «service» — и напечатать их номера: без номера их не внести в
     SERVICE_ACCOUNT_IDS;
  2. взять ВСЕ карточки, которые эти записи создали или ведут, фильтром по
     самой записи, а не выборкой из окна;
  3. на этих карточках проверить каждый носитель инициатора:
       - поля карточки: ответственный, кто двигал, кто менял, последняя
         активность;
       - строковые поля карточки, похожие на почту отправителя, — если робот
         шлёт письмо из ящика сорсера, почта и есть инициатор;
       - родительская сделка: стандартные поля «кто» и ВСЕ пользовательские
         поля сделки типа «сотрудник» — в сделке может уже стоять «Сорсер»;
  4. по каждому носителю — доля карточек, где он указывает на сотрудника
     отдела поиска поставщиков. Носитель с долей около ста процентов и есть
     искомый механизм; ниже — честная граница того, что возможно.

ПЕЧАТАЮТСЯ ТОЛЬКО АГРЕГАТЫ (CLAUDE.md, правило 17): коды и заголовки полей,
счётчики и доли. Ни почт, ни имён людей, ни номеров карточек и сделок. Номера и
имена печатаются лишь для записей, чьё имя само называет их служебными.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from bitrix_client import BitrixClient  # noqa: E402

SPA = config.SPA_ENTITY_TYPE_ID
CATEGORY = config.SPA_CATEGORY_ID
DEPT = config.DEPT_SOURCING_ID
WINDOW = os.getenv("PROBE_FROM") or config.DEFAULT_PERIOD_ANCHOR
LIMIT = int(os.getenv("PROBE_LIMIT") or 3000)

# Имя, которое само называет запись служебной. Закрытый список: живого человека
# по этому признаку не спутать, а номер служебной записи печатать можно.
СЛУЖЕБНОЕ_ИМЯ = re.compile(r"служебн|аккаунт|робот|\bбот\b|robot|service|webhook|интеграц", re.I)
ПОХОЖЕ_НА_ПОЧТУ = re.compile(r"mail|почт|отправ|sender|from", re.I)
ПОЧТА = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def доля(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else f"{part}/0"


def заголовок(t: str) -> None:
    print()
    print(t)
    print("-" * len(t), flush=True)


def пусто(v) -> bool:
    """Портал отдаёт пустоту и строкой «0», и «None», и пустым списком."""
    if isinstance(v, dict):
        return not v
    if isinstance(v, (list, tuple, set)):
        return not any(not пусто(x) for x in v)
    return str(v or "") in ("", "0", "None")


def след(items: list[dict], поле: str, dept: set[str]) -> tuple[int, int, int]:
    """(заполнено, указывает в отдел, отличается от ответственного)."""
    есть = в_отделе = иначе = 0
    for i in items:
        v = i.get(поле)
        if пусто(v):
            continue
        v = str(v)
        есть += 1
        if v in dept:
            в_отделе += 1
        if v != str(i.get("assignedById") or ""):
            иначе += 1
    return есть, в_отделе, иначе


def найти_служебные(c: BitrixClient) -> tuple[dict[str, str], dict[str, str]]:
    """(служебные по имени: id → имя), (все: почта → id)."""
    заголовок("1. СЛУЖЕБНЫЕ ЗАПИСИ ПО ИМЕНИ")
    deps = {str(d.get("ID")): str(d.get("NAME") or "") for d in c.departments()}
    служебные: dict[str, str] = {}
    по_почте: dict[str, str] = {}
    всего = 0
    for u in c.list_paged("user.get", {}):
        всего += 1
        uid = str(u.get("ID"))
        имя = " ".join(x for x in (u.get("NAME"), u.get("LAST_NAME"), u.get("SECOND_NAME")) if x).strip()
        mail = str(u.get("EMAIL") or "").strip().lower()
        if mail:
            по_почте[mail] = uid
        if СЛУЖЕБНОЕ_ИМЯ.search(имя):
            d = u.get("UF_DEPARTMENT") or []
            отдел = deps.get(str(d[0]), "") if d else ""
            служебные[uid] = f"{имя} · {отдел or 'подразделение не указано'}"
    print(f"  учётных записей портала: {всего}; с почтой: {len(по_почте)}")
    if not служебные:
        print("  ни одна запись не называет себя служебной по имени")
    for uid, имя in служебные.items():
        print(f"  #{uid:6} {имя}")
    return служебные, по_почте


def карточки_записи(c: BitrixClient, uid: str, select: list[str]) -> list[dict]:
    """Все карточки, которые запись создала ИЛИ ведёт, в отчётном окне."""
    out: dict[str, dict] = {}
    for роль in ("createdBy", "assignedById"):
        items = c.list_items(SPA, filter={"categoryId": CATEGORY, роль: int(uid),
                                          ">=createdTime": f"{WINDOW}T00:00:00+03:00"},
                             select=select, max_items=LIMIT)
        for i in items:
            out[str(i["id"])] = i
    return list(out.values())


def поля_почты(c: BitrixClient) -> list[tuple[str, str]]:
    try:
        res = c.call("crm.item.fields", {"entityTypeId": SPA}) or {}
    except Exception:                                        # noqa: BLE001
        return []
    fields = res.get("fields") or res
    return [(code, str(m.get("title") or "")) for code, m in fields.items()
            if isinstance(m, dict) and str(m.get("type")) in ("string", "text")
            and (ПОХОЖЕ_НА_ПОЧТУ.search(str(m.get("title") or "")) or ПОХОЖЕ_НА_ПОЧТУ.search(code))]


def поля_сотрудника_сделки(c: BitrixClient) -> list[tuple[str, str]]:
    """Пользовательские поля сделки типа «сотрудник»: вдруг там уже есть «Сорсер»."""
    try:
        res = c.list_paged("crm.deal.userfield.list", {"filter": {"USER_TYPE_ID": "employee"}})
    except Exception as e:                                   # noqa: BLE001
        print(f"  crm.deal.userfield.list: {e.__class__.__name__}")
        return []
    out = []
    for f in res:
        code = str(f.get("FIELD_NAME") or "")
        label = f.get("EDIT_FORM_LABEL") or f.get("LIST_COLUMN_LABEL") or ""
        if isinstance(label, dict):
            label = label.get("ru") or label.get("en") or next(iter(label.values()), "")
        out.append((code, str(label) or code))
    return out


def разбор_записи(c: BitrixClient, uid: str, имя: str, dept: set[str],
                  по_почте: dict[str, str], почтовые: list[tuple[str, str]],
                  поля_сделки: list[tuple[str, str]]) -> None:
    заголовок(f"2. КАРТОЧКИ ЗАПИСИ #{uid} · {имя}")
    select = ["id", "assignedById", "createdBy", "updatedBy", "movedBy", "lastActivityBy",
              "createdTime", "parentId2", *[c_ for c_, _ in почтовые]]
    items = карточки_записи(c, uid, select)
    n = len(items)
    сама_создала = sum(1 for i in items if str(i.get("createdBy")) == uid)
    сама_ведёт = sum(1 for i in items if str(i.get("assignedById")) == uid)
    print(f"  карточек с {WINDOW}: {n}; создала {сама_создала}; ответственная {сама_ведёт}")
    if not n:
        return
    даты = [(i.get("createdTime") or "")[:10] for i in items if i.get("createdTime")]
    if даты:
        print(f"  первая карточка {min(даты)}, последняя {max(даты)}")

    заголовок("3. НОСИТЕЛИ ИНИЦИАТОРА НА САМОЙ КАРТОЧКЕ")
    print(f"  {'поле':18} {'заполнено':>18} {'ведёт в отдел':>18} {'≠ ответственного':>18}")
    лучшие: list[tuple[str, int]] = []
    for поле in ("assignedById", "movedBy", "updatedBy", "lastActivityBy"):
        есть, в_отделе, иначе = след(items, поле, dept)
        лучшие.append((f"карточка.{поле}", в_отделе))
        print(f"  {поле:18} {доля(есть, n):>18} {доля(в_отделе, n):>18} {доля(иначе, n):>18}")

    for code, title in почтовые:
        есть = совпало = в_отделе = 0
        for i in items:
            m = ПОЧТА.search(str(i.get(code) or ""))
            if not m:
                continue
            есть += 1
            u = по_почте.get(m.group(0).lower())
            if u:
                совпало += 1
                if u in dept:
                    в_отделе += 1
        лучшие.append((f"почта «{title}»", в_отделе))
        print(f"  почта «{title}» ({code}): заполнена {доля(есть, n)}, "
              f"совпала с сотрудником {доля(совпало, n)}, ведёт в отдел {доля(в_отделе, n)}")
    if not почтовые:
        print("  полей, похожих на почту отправителя, в СП-166 нет")

    заголовок("4. РОДИТЕЛЬСКАЯ СДЕЛКА")
    с_сделкой = [i for i in items if not пусто(i.get("parentId2"))]
    ids = sorted({str(i["parentId2"]) for i in с_сделкой})
    print(f"  карточек с родительской сделкой: {доля(len(с_сделкой), n)}; различных сделок: {len(ids)}")
    if ids:
        std = ["ASSIGNED_BY_ID", "MOVED_BY_ID", "MODIFY_BY_ID", "CREATED_BY_ID"]
        uf = [code for code, _ in поля_сделки]
        try:
            сделки = c.deals_by_ids(ids, select=["ID", *std, *uf])
        except Exception as e:                               # noqa: BLE001
            print(f"  выгрузка сделок не удалась: {e.__class__.__name__}")
            сделки = {}
        подписи = dict(поля_сделки)
        for поле in [*std, *uf]:
            есть = в_отделе = 0
            for i in items:
                d = сделки.get(str(i.get("parentId2"))) or {}
                v = d.get(поле)
                if isinstance(v, list):
                    v = v[0] if v else ""
                if пусто(v):
                    continue
                есть += 1
                if str(v) in dept:
                    в_отделе += 1
            имя_поля = подписи.get(поле, поле)
            лучшие.append((f"сделка «{имя_поля}»", в_отделе))
            print(f"  {имя_поля[:34]:34} {доля(есть, n):>18}   ведёт в отдел {доля(в_отделе, n):>18}")

    заголовок("5. ИТОГ ПО ЗАПИСИ")
    лучшие.sort(key=lambda x: -x[1])
    for носитель, k in лучшие[:6]:
        print(f"  {доля(k, n):>18}  {носитель}")
    print("  Носитель с долей около ста процентов — искомый механизм. Ниже — граница")
    print("  того, что возможно без изменения структуры портала.")


def main() -> int:
    url = (os.getenv("BITRIX_WEBHOOK_URL") or "").strip()
    if not url:
        print("нет BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 2
    c = BitrixClient(url)
    print("ЗОНД v46 · кто из сорсеров стоит за каждой карточкой робота")
    служебные, по_почте = найти_служебные(c)
    dept = c.dept_member_ids(DEPT)
    print(f"  сотрудников отдела поиска поставщиков (с дочерними): {len(dept)}")

    почтовые = поля_почты(c)
    print(f"  строковых полей СП-166, похожих на почту: {len(почтовые)}")
    for code, title in почтовые:
        print(f"    {code:28} {title}")
    поля_сделки = поля_сотрудника_сделки(c)
    print(f"  полей сделки типа «сотрудник»: {len(поля_сделки)}")
    for code, title in поля_сделки:
        print(f"    {code:28} {title}")

    # разбираем и служебные по имени, и заданные в SERVICE_ACCOUNT_IDS
    к_разбору = dict(служебные)
    for uid in sorted(config.SERVICE_ACCOUNT_IDS):
        к_разбору.setdefault(uid, "(задана в SERVICE_ACCOUNT_IDS)")
    for uid, имя in к_разбору.items():
        разбор_записи(c, uid, имя, dept, по_почте, почтовые, поля_сделки)
    print()
    print("Замер, записи не было.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
