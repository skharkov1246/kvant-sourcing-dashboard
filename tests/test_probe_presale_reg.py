"""Зонд v62 «Пресейл по регламенту сорсера»: что дало бы неверный вывод или утечку.

1. Журнал публичный: ни номеров сделок и карточек, ни названий, ни людей, ни подписей
   вариантов с юрлицом и вариантов не процессных полей, ни текста отказа портала.
   Первая строка — «ожидается: …», последняя — сводка_нагрузки().
2. Стадия → шаг инструкции v1.4 по имени; отказ — без шага, успех — 11.
3. Пребывание в стадии: выход — следующий вход; последний — «идёт» только у текущей стадии.
4. Ворота: n ≥ 30 и покрытие ≥ 80 % — показать; n ≥ 10 — предварительно; иначе скрыть.
5. Дожим карточки — от первого исходящего письма в рабочих днях; ответ раньше письма не срок.
6. Запасные пути: дела без фильтра массивом — обход окна; история СП-166 с отказом — movedTime.
7. Смета больше предела — прогон не начат; probe.yml: зонд в закрытом списке `script`.

Корпус придуман (правило 18): номера, названия и суммы нарочно особые.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kvant_probe_presale_reg", ROOT / "scripts" / "probe_presale_reg.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
bc = m.bc

СЕГОДНЯ = dt.date(2026, 10, 30)
ЗАПРЕТНОЕ = ("77100", "77200", "77300", "Альфа", "Ромашка", "Иванов", "Доступ запрещён", "9876543", "ООО",
             "Учебн", "http")


def t(s: str) -> str:
    return s if "T" in s else s + "T10:00:00+03:00"


# ── придуманный портал ──────────────────────────────────────────────────────────────────────

СТАДИИ = (("NEW", "Новый запрос", None), ("PREPARATION", "Назначение сорсера", None),
          ("UC_TZ", "Проработка ТЗ и вопросы заказчику", None), ("UC_SRCH", "Поиск поставщиков и сбор КП", None),
          ("UC_AN", "Анализ КП и фиксация поставщиков", None), ("UC_CMP", "Сравнение предложений и расчёт ЭП", None),
          ("UC_ECON", "Экономика готова | Можно подавать", None), ("UC_TND", "Тендерная процедура", None),
          ("UC_FIX", "Доработка по обратной связи", None),
          ("UC_DONE", "Сорсинг завершен / Ожидаем решение заказчика", None),
          ("UC_SPEC", "Согласование финальной спецификации", None), ("WON", "Сделка выиграна", "S"),
          ("LOSE", "Экономически не интересно", "F"), ("UC_LATE", "Вовремя не подались", "F"))
ПОЛЯ = {
    "TITLE": {"type": "string", "title": "Название"},
    "UF_CRM_7001": {"type": "enumeration", "formLabel": "Deal type (Presale)", "isMultiple": False,
                    "items": [{"ID": "501", "VALUE": "Расценка"}, {"ID": "502", "VALUE": "Комплексная поставка"},
                              {"ID": "503", "VALUE": "ООО Ромашка"}]},
    "UF_CRM_7002": {"type": "boolean", "formLabel": "ЗИП"},
    "UF_CRM_7003": {"type": "datetime", "formLabel": "End date and time for receiving offer"},
    "UF_CRM_7004": {"type": "enumeration", "formLabel": "Status of questions for the customer",
                    "items": [{"ID": "511", "VALUE": "Вопросов нет"}, {"ID": "512", "VALUE": "Ждём ответа"}]},
    m.config.ECON_READY_FIELD: {"type": "enumeration", "formLabel": "Экономика готова",
                                "items": [{"ID": "8834", "VALUE": "Да"}, {"ID": "8836", "VALUE": "Нет"}]},
    "UF_CRM_7005": {"type": "enumeration", "formLabel": "Suppliers", "isMultiple": True,
                    "items": [{"ID": "601", "VALUE": "Альфа Трейд"}]},
    "UF_CRM_7006": {"type": "money", "formLabel": "Offer sum and currency of selected supplier"},
    m.presale.SOURCER_F: {"type": "employee", "formLabel": "Сорсер"},
}


def сделка(i, stage, created, **к):
    d = {"ID": str(i), "TITLE": "Учебная поставка Альфа", "CATEGORY_ID": "32", "STAGE_ID": f"C32:{stage}",
         "STAGE_SEMANTIC_ID": "P", "DATE_CREATE": t(created), "OPPORTUNITY": "9876543.21", "UF_CRM_7005": ["601"],
         "UF_CRM_7006": "9876543|EUR", m.presale.SOURCER_F: "4242"}
    d.update(к)
    return d


def портал(n_прочих: int = 30) -> dict:
    сделки = [
        # «Расценка»: 3, 5, 6 пропущены; 7 → 9 за 1 р.д. (пт 04.09 → пн 07.09)
        сделка(7710001, "UC_DONE", "2026-09-01", UF_CRM_7001="501", UF_CRM_7003=t("2026-09-05"),
               **{m.config.ECON_READY_FIELD: "8834"}),
        # ЗИП: сейчас на «Экономика готова»
        сделка(7710002, "UC_ECON", "2026-09-02", UF_CRM_7002="1", UF_CRM_7001="502"),
        # прочие: возврат 9 → 8
        сделка(7710003, "UC_DONE", "2026-09-03", UF_CRM_7001="502", UF_CRM_7004="511"),
        сделка(7710004, "LOSE", "2026-09-03", STAGE_SEMANTIC_ID="F", UF_CRM_7001="503"),
    ]
    ист = {
        "7710001": [("NEW", "2026-09-01T09:00"), ("PREPARATION", "2026-09-01T12:00"), ("UC_SRCH", "2026-09-02"),
                    ("UC_ECON", "2026-09-04"), ("UC_DONE", "2026-09-07")],
        "7710002": [("NEW", "2026-09-02T09:00"), ("PREPARATION", "2026-09-02T11:00"), ("UC_TZ", "2026-09-02T15:00"),
                    ("UC_SRCH", "2026-09-03"), ("UC_AN", "2026-09-08"), ("UC_CMP", "2026-09-10"),
                    ("UC_ECON", "2026-09-11")],
        "7710003": [("NEW", "2026-09-03T09:00"), ("PREPARATION", "2026-09-03T11:00"), ("UC_TZ", "2026-09-03T15:00"),
                    ("UC_SRCH", "2026-09-04"), ("UC_AN", "2026-09-09"), ("UC_CMP", "2026-09-11"),
                    ("UC_ECON", "2026-09-15"), ("UC_DONE", "2026-09-16"), ("UC_FIX", "2026-09-18"),
                    ("UC_DONE", "2026-09-21")],
        "7710004": [("NEW", "2026-09-03T09:00"), ("UC_SRCH", "2026-09-04"), ("LOSE", "2026-09-20")],
    }
    for k in range(n_прочих):           # массовка: чтобы части были, а ворота что-то пропускали
        i = 7710100 + k
        сделки.append(сделка(i, "UC_ECON", "2026-09-14", UF_CRM_7001="502"))
        ист[str(i)] = [("NEW", "2026-09-14T09:00"), ("PREPARATION", "2026-09-14T10:00"), ("UC_TZ", "2026-09-14T11:00"),
                       ("UC_SRCH", "2026-09-16"), ("UC_AN", "2026-09-17"), ("UC_CMP", "2026-09-22"),
                       ("UC_ECON", "2026-09-23")]
    карточки, ист_к, дела = [], {}, []

    def карточка(cid, did, stage, created, offer=False, moved=None):
        карточки.append({"id": cid, "parentId2": did, "categoryId": 24, "stageId": f"DT166_24:{stage}",
                         "createdTime": t(created), "movedTime": t(moved or created), "title": "Запрос Альфа",
                         m.OFFER_F: [{"id": 1, "url": "https://example.invalid/file"}] if offer else []})

    карточка(7720001, 7710001, "UC_H49RUE", "2026-09-02", offer=True, moved="2026-09-08")
    ист_к["7720001"] = [("DT166_24:NEW", "2026-09-02T09:00"), ("DT166_24:PREPARATION", "2026-09-02T10:00"),
                        ("DT166_24:UC_H49RUE", "2026-09-08")]
    дела += [(7730001, 7720001, "4", "CRM_EMAIL", "2", "2026-09-02T10:05"),
             (7730002, 7720001, "4", "CRM_EMAIL", "2", "2026-09-04T10:05"),
             (7730003, 7720001, "2", "VOXIMPLANT_CALL", "2", "2026-09-07T10:05")]
    карточка(7720002, 7710002, "PREPARATION", "2026-09-03", moved="2026-09-03")
    ист_к["7720002"] = [("DT166_24:NEW", "2026-09-03T09:00"), ("DT166_24:PREPARATION", "2026-09-03T10:00")]
    дела += [(7730004, 7720002, "4", "CRM_EMAIL", "2", "2026-09-03T10:05")]
    карточка(7720003, 7710002, "3", "2026-09-03", moved="2026-09-17")
    ист_к["7720003"] = [("DT166_24:NEW", "2026-09-03T09:00"), ("DT166_24:PREPARATION", "2026-09-03T10:00"),
                        ("DT166_24:3", "2026-09-17")]
    дела += [(7730005, 7720003, "4", "CRM_EMAIL", "2", "2026-09-03T10:05")]
    карточка(7720004, 7710003, "SUCCESS", "2026-09-04", offer=True, moved="2026-09-10")
    ист_к["7720004"] = [("DT166_24:NEW", "2026-09-04T09:00"), ("DT166_24:PREPARATION", "2026-09-04T10:00"),
                        ("DT166_24:UC_H49RUE", "2026-09-08"), ("DT166_24:SUCCESS", "2026-09-10")]
    дела += [(7730006, 7720004, "4", "CRM_EMAIL", "2", "2026-09-04T10:05")]
    for k in range(n_прочих):
        cid, did = 7720100 + k, 7710100 + k
        карточка(cid, did, "UC_H49RUE", "2026-09-16", offer=True, moved="2026-09-18")
        ист_к[str(cid)] = [("DT166_24:NEW", "2026-09-16T09:00"), ("DT166_24:PREPARATION", "2026-09-16T10:00"),
                           ("DT166_24:UC_H49RUE", "2026-09-18")]
        дела.append((7731000 + k, cid, "4", "CRM_EMAIL", "2", "2026-09-16T10:05"))
    return {"сделки": сделки, "ист": ист, "карточки": карточки, "ист_к": ист_к,
            "дела": [{"ID": str(a), "OWNER_ID": str(o), "OWNER_TYPE_ID": "166", "TYPE_ID": tp, "PROVIDER_ID": pr,
                      "DIRECTION": dr, "CREATED": cr} for a, o, tp, pr, dr, cr in дела]}


class Фейк:
    """Придуманный портал: методы, которые читает зонд, с фильтрами, которые он ставит."""

    list_paged = bc.BitrixClient.list_paged
    list_deals_fast = bc.BitrixClient.list_deals_fast
    spa_stage_meta = bc.BitrixClient.spa_stage_meta

    def __init__(self, данные: dict, *, дела_без_фильтра=False, история166_отказ=False, total_сделок=None):
        self.д = данные
        self.дела_без_фильтра = дела_без_фильтра
        self.история166_отказ = история166_отказ
        self.total_сделок = total_сделок
        self.вызовы: list[str] = []

    def call(self, method, params=None, **_k):
        return self.call_envelope(method, params).get("result")

    def call_envelope(self, method, params=None, **_k):
        bc._учесть("запросов")
        self.вызовы.append(method)
        p = params or {}
        f = p.get("filter") or {}
        if method == "crm.category.list":
            return {"result": {"categories": [{"id": 0, "name": "Реализация"}, {"id": 14, "name": "Ромашка и сыновья"},
                                              {"id": 32, "name": "Пресейл"}]}}
        if method == "crm.deal.list":
            рр = [d for d in self.д["сделки"] if str(d["CATEGORY_ID"]) == str(f.get("CATEGORY_ID", d["CATEGORY_ID"]))
                  and int(d["ID"]) > int(f.get(">ID", 0))
                  and (not f.get(">=DATE_CREATE") or d["DATE_CREATE"] >= f[">=DATE_CREATE"])]
            рр.sort(key=lambda d: int(d["ID"]))
            if p.get("start") == 0:
                return {"result": [], "total": self.total_сделок if self.total_сделок is not None else len(рр)}
            return {"result": рр[:50]}
        if method == "crm.status.list":
            ent = f.get("ENTITY_ID")
            if ent == "DEAL_STAGE_32":
                return {"result": [{"STATUS_ID": f"C32:{s}", "NAME": n, "SORT": 10 * (i + 1), "SEMANTICS": sem}
                                   for i, (s, n, sem) in enumerate(СТАДИИ)]}
            if ent == "DYNAMIC_166_STAGE_24":
                return {"result": [{"STATUS_ID": "DT166_24:NEW", "NAME": "New Request", "SORT": 10},
                                   {"STATUS_ID": "DT166_24:PREPARATION", "NAME": "Request Sent", "SORT": 20},
                                   {"STATUS_ID": "DT166_24:UC_H49RUE", "NAME": "Price at Work", "SORT": 30},
                                   {"STATUS_ID": "DT166_24:SUCCESS", "NAME": "Selected", "SORT": 40,
                                    "SEMANTICS": "S"},
                                   {"STATUS_ID": "DT166_24:3", "NAME": "Ответ не получен (в срок)", "SORT": 50,
                                    "SEMANTICS": "F"}]}
            return {"result": []}
        if method == "crm.deal.fields":
            return {"result": ПОЛЯ}
        if method == "crm.item.list":
            свои = {str(x) for x in f.get("@parentId2") or []}
            рр = [c for c in self.д["карточки"] if str(c["parentId2"]) in свои and c["id"] > int(f.get(">id", 0))]
            return {"result": {"items": sorted(рр, key=lambda c: c["id"])[:50]}}
        if method == "crm.stagehistory.list":
            if p.get("entityTypeId") == 166 and self.история166_отказ:
                raise bc.BitrixError("crm.stagehistory.list: ACCESS_DENIED Доступ запрещён Иванов")
            ист = self.д["ист"] if p.get("entityTypeId") == 2 else self.д["ист_к"]
            свои = {str(x) for x in f.get("OWNER_ID") or []}
            стадии = set(f.get("STAGE_ID") or [])
            items = []
            for o, tr in ист.items():
                if o not in свои:
                    continue
                for s, tt in tr:
                    sid = f"C32:{s}" if p.get("entityTypeId") == 2 else s
                    if стадии and sid not in стадии:
                        continue
                    items.append({"ID": str(len(items) + 1), "OWNER_ID": o, "STAGE_ID": sid,
                                  "CREATED_TIME": tt if "+" in tt else tt + ":00+03:00"})
            return {"result": {"items": items}, "total": len(items)}
        if method == "crm.activity.list":
            рр = [a for a in self.д["дела"] if int(a["ID"]) > int(f.get(">ID", 0))]
            if f.get("OWNER_ID") is not None and not self.дела_без_фильтра:
                свои = {str(x) for x in f["OWNER_ID"]}
                рр = [a for a in рр if a["OWNER_ID"] in свои]
            elif f.get("OWNER_ID") is not None:                       # фильтр отброшен: чужие строки
                рр = рр + [{"ID": str(7739000 + k), "OWNER_ID": str(7729000 + k), "OWNER_TYPE_ID": "166",
                            "TYPE_ID": "4", "PROVIDER_ID": "CRM_EMAIL", "DIRECTION": "2",
                            "CREATED": "2026-09-20T10:00:00+03:00"} for k in range(10)]
            if "TYPE_ID" in f:
                рр = [a for a in рр if a["TYPE_ID"] == str(f["TYPE_ID"])]
            рр.sort(key=lambda a: int(a["ID"]))
            if p.get("start") == 0:
                return {"result": [], "total": len(рр)}
            return {"result": рр[:50]}
        if method == "tasks.task.list":
            raise bc.BitrixError("tasks.task.list: ACCESS_DENIED Доступ запрещён: Иванов")
        raise AssertionError(f"неожиданный метод {method}")


def прогон(capsys, **к) -> tuple[int, list[str]]:
    код = m.main(Фейк(портал(), **к), СЕГОДНЯ)
    return код, capsys.readouterr().out.splitlines()


# ── 1. журнал ───────────────────────────────────────────────────────────────────────────────

def test_журнал_только_агрегаты(capsys, monkeypatch):
    monkeypatch.setenv("PROBE_PARTS", "10")
    код, строки = прогон(capsys)
    текст = "\n".join(строки)
    assert код == 0
    assert строки[0].startswith("ожидается: воронка пресейла #32, сделок 34")
    assert строки[-1].startswith("портал: запросов")
    for слово in ЗАПРЕТНОЕ:
        assert слово not in текст, слово
    for раздел in ("1. ПОЛЯ СДЕЛКИ", "2. СТАДИИ", "3. ИСТОРИЯ", "4. КП → ТКП", "5. ДОЖИМ", "6. СРОК", "7. ЗАДАЧИ",
                   "СВОДКА ВЕРДИКТОВ"):
        assert раздел in текст, раздел
    assert "значения: «Расценка» 1 · «Комплексная поставка» 32 · «вариант 3» 1" in текст   # юрлицо — номером
    assert "значения: да 1 · нет / пусто 33" in текст and "нет в справочнике" not in текст
    assert "право вебхука: нет (ACCESS_DENIED)" in текст
    assert "ROS" not in текст and "Альфа Трейд" not in текст          # варианты «Suppliers» не печатаются


def test_шаги_и_пропуски(capsys):
    _код, строки = прогон(capsys)
    текст = "\n".join(строки)
    assert "Тендерная процедура» · P · вне регламента v1.4" in текст
    assert "Расценка: дошли 1; пропустили все три 1 из 1 (100 %)" in текст
    assert "возвраты (шаг назад): 1 у" in текст and "9→8 1" in текст
    # «Расценка» — 7 → 9 за 1 р.д.; КП-файл у неё есть, старый способ ТКП по стадии не видит
    assert "старый способ (stages.deal_reached_tkp) не видит 2 из 2" in текст
    assert "способ сроков: история стадий СП-166" in текст
    assert "дела карточек: OWNER_ID массивом" in текст


def test_дожим_по_письмам(capsys):
    _код, строки = прогон(capsys)
    текст = "\n".join(строки)
    # карточка 1: второе письмо на 2-й р.д., звонок на 3-й; карточки 2 и 3 — без второго письма
    assert "второе письмо к 2-му р.д. 1 из 3" in текст
    assert "звонок к 3-му р.д. 1 из 3" in текст


# ── 2–5. чистые функции ─────────────────────────────────────────────────────────────────────

def test_стадия_в_шаг():
    for _s, имя, sem in СТАДИИ:
        шаг = m.шаг_стадии(имя, sem or "P")
        if sem == "F":
            assert шаг is None
        elif sem == "S":
            assert шаг == 11
    assert m.шаг_стадии("Сравнение предложений и расчет ЭП", "P") == 6
    assert m.шаг_стадии("Сорсинг завершён / Ожидаем решение заказчика", "P") == 9
    assert m.шаг_стадии("Анализ КП и фиксация поставщиков", "P") == 5
    assert m.шаг_стадии("Тендерная процедура", "P") is None


def test_пребывания():
    шаги = {"A": 3, "B": 4}
    пп = m.пребывания([("A", "2026-09-04T10:00:00+03:00"), ("B", "2026-09-07T10:00:00+03:00")], шаги, "B", СЕГОДНЯ)
    assert пп[0]["рд"] == 1 and not пп[0]["идёт"]
    assert пп[1]["идёт"] and пп[1]["рд"] == m.period_mod.workdays_ru(dt.date(2026, 9, 7), СЕГОДНЯ)
    ушла = m.пребывания([("A", "2026-09-04T10:00:00+03:00")], шаги, "C99:OTHER", СЕГОДНЯ)
    assert ушла[0]["выход"] is None and not ушла[0]["идёт"] and ушла[0]["рд"] is None


def test_ворота():
    assert m.вердикт(30, 0.8) == "показать"
    assert m.вердикт(30, 0.79) == "предварительно"
    assert m.вердикт(10, 1.0) == "предварительно"
    assert m.вердикт(9, 1.0) == "скрыть"
    assert m.вердикт(40, None) == "показать"


def test_разбор_карточки():
    def ч(s):
        return dt.datetime.fromisoformat(s + "+03:00")
    r = m.разбор_карточки([ч("2026-09-02T10:00"), ч("2026-09-04T10:00")], [ч("2026-09-07T09:00")],
                          [("DT166_24:PREPARATION", "2026-09-02T09:00:00+03:00"),
                           ("DT166_24:UC_H49RUE", "2026-09-08T09:00:00+03:00")], СЕГОДНЯ)
    assert (r["second"], r["call"], r["reply"], r["kp"]) == (2, 3, 4, 4)
    assert m.без_ответа_к(r, 2, "paw") and not m.без_ответа_к(r, 4, "paw")     # ответ на 4-й р.д.
    assert not m.без_ответа_к(dict(r, reply=1), 2, "paw") and m.без_ответа_к(dict(r, reply=None), 2, "paw")
    раньше = m.разбор_карточки([ч("2026-09-08T10:00")], [], [("DT166_24:UC_61BSRU", "2026-09-04T09:00:00+03:00")],
                               СЕГОДНЯ)
    assert раньше["before"] and раньше["reply"] is None
    без = m.разбор_карточки([ч("2026-09-02T10:00")], [], None, СЕГОДНЯ)
    assert m.без_ответа_к(без, 2, "sent") and not m.без_ответа_к(без, 2, "paw")


def test_до_срока():
    t = dt.datetime(2026, 9, 5, 15, 0, tzinfo=m.MSK)
    assert m.до_срока(t, "2026-09-05") is True                      # срок днём — весь день
    assert m.до_срока(t, "2026-09-05T00:00:00+03:00") is True       # полночь — как день
    assert m.до_срока(t, "2026-09-05T12:00:00+03:00") is False
    assert m.до_срока(t, "") is None and m.до_срока(t, "не дата") is None


def test_поля_по_подписи():
    поля = m.найти_поля(ПОЛЯ)
    assert [f["code"] for f in поля["dealType"]] == ["UF_CRM_7001"]
    assert поля["zip"][0]["type"] == "boolean"
    assert поля["econReady"][0]["code"] == m.config.ECON_READY_FIELD
    assert "sourcer" in поля and "suppliers" in поля and "tkp" not in поля
    assert m.метки_вариантов(ПОЛЯ["UF_CRM_7001"]["items"]) == {"501": "Расценка", "502": "Комплексная поставка",
                                                                "503": "вариант 3"}


# ── 6. запасные пути ────────────────────────────────────────────────────────────────────────

def test_дела_без_фильтра_массивом_окном(capsys):
    код, строки = прогон(capsys, дела_без_фильтра=True)
    текст = "\n".join(строки)
    assert код == 0
    assert "фильтр OWNER_ID массивом не сработал — дела читаются одним обходом окна" in текст
    assert "дела карточек: одним обходом окна" in текст
    assert "второе письмо к 2-му р.д. 1 из 3" in текст              # чужие дела в счёт не вошли


def test_история_сп166_с_отказом_movedtime(capsys):
    код, строки = прогон(capsys, история166_отказ=True)
    текст = "\n".join(строки)
    assert код == 0
    assert "история стадий СП-166: не читается (отказ портала ACCESS_DENIED)" in текст
    assert "movedTime — история СП-166 не прочитана" in текст
    for слово in ЗАПРЕТНОЕ:
        assert слово not in текст, слово


# ── 7. смета и probe.yml ────────────────────────────────────────────────────────────────────

def test_смета_больше_предела_прогон_не_начат(capsys):
    код, строки = прогон(capsys, total_сделок=20000)
    assert код == 1
    assert строки[0].startswith("ожидается:") and "прогон не начат" in "\n".join(строки)
    assert строки[-1].startswith("портал: запросов")


def test_смета_в_пределах_ориентира():
    assert m.смета(250, 240, 10) <= m.БЮДЖЕТ["ожидается"]
    assert m.смета(250, 240, 50) <= m.БЮДЖЕТ["предел"]


def test_части_входом(monkeypatch):
    monkeypatch.setenv("PROBE_PARTS", "3")
    assert m.частей()[0] == 10
    monkeypatch.setenv("PROBE_PARTS", "30")
    assert m.частей()[0] == 50
    assert m.разбить(["5", "1", "3", "2", "4"], 2) == [["1", "2", "3"], ["4", "5"]]


def test_зонд_в_probe_yml():
    wf = yaml.safe_load((ROOT / ".github/workflows/probe.yml").read_text(encoding="utf-8"))
    вход = (wf.get("on") or wf[True])["workflow_dispatch"]["inputs"]
    assert "probe_presale_reg.py" in вход["script"]["options"]
    шаг = next(s for s in wf["jobs"]["probe"]["steps"] if s.get("name") == "Зонд")
    assert шаг["env"]["PROBE_PARTS"] and шаг["env"]["BITRIX_RPS"] == "1.0"
    assert wf["concurrency"]["group"] == "bitrix-portal"
