"""Зонд правил КП: роль стадии по имени и расхождения с правилами. Корпус придуман (правило 18)."""
from __future__ import annotations

import datetime as dt
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from scripts import probe_kp_rules as зонд  # noqa: E402


def test_роль_стадии_по_имени():
    имена = {"Price at Work": "price_at_work", "Selected": "selected", "Not Selected": "not_selected",
             "Не прошли по цене": "lost_price", "Не подошло по технике": "lost_tech", "КП получено": "kp_legacy",
             "In Correspondance": "correspondence", "Отказ в КП": "refused", "Ответ не получен (в срок)": "no_answer",
             "Unread Messages": "unread", "Request Sent": "sent", "New Request": "new", "Что-то": "прочее"}
    for имя, r in имена.items():
        assert зонд.роль(имя) == r, имя


def test_расхождения():
    роли = {"S1": "sent", "P": "price_at_work", "K": "kp_legacy", "R": "refused"}
    f = зонд.OFFER_FIELD
    карточки = [
        {"stageId": "S1", f: [{"id": 1}], "movedTime": "2026-10-18T10:00:00+03:00"},
        {"stageId": "P", f: [], "movedTime": "2026-09-01T10:00:00+03:00"},
        {"stageId": "K", f: [{"id": 2}], "movedTime": "2026-10-19T10:00:00+03:00"},
        {"stageId": "R", f: [{"id": 3}], "movedTime": "2026-10-01T10:00:00+03:00"},
    ]
    по, возр, расх = зонд.разбор_карточек(карточки, роли, [], dt.date(2026, 10, 19))
    assert по["sent"]["с файлом Offer"] == 1 and по["price_at_work"]["всего"] == 1
    assert расх["файл КП есть, стадия до КП"] == 1
    assert расх["стадия КП, файла нет (не засчитается)"] == 1
    assert расх["Price at Work без итога дольше 14 дн."] == 1
    assert расх["«КП получено» после 19.10.2026"] == 1
    assert расх["файл КП есть, стадия отказа/молчания"] == 1
    assert возр["31–90 дн."] == 1
