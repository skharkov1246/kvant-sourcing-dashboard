"""Зонд обязан отвечать на свой вопрос, а не подтверждать гипотезу.

scripts/bitrix_probe.py v44 выбирает между двумя причинами непрочитанных вложений:
нет права `disk` (чинит владелец одной галочкой) или неверный путь до байтов (чиню
я). Разница в исполнителе, поэтому итог зонда вынесен чистой функцией и проверяется
здесь: в прежнем виде строка «нет скоупа disk или файла нет» держала обе гипотезы
сразу и не была измерением ни одной.

Отдельно закрыт отказ измерения: права не прочитались — это НЕ «тупик». Молчаливое
превращение непрочитанного в отрицательный ответ — та же ошибка, что стоила мне
пометки «прошёл» там, где положительный контроль вернул 404.

Корпус придуман, а не взят из описи, как велят правила репозитория.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


def probe():
    import bitrix_probe
    return bitrix_probe


def test_каждый_случай_итога_имеет_текст():
    p = probe()
    for case in ("нет права", "право есть, путь мой", "обход без диска", "тупик", "не измерено"):
        assert p.VERDICTS[case], f"случай {case} без текста"


def test_нет_права_disk_называет_действие_владельца():
    p = probe()
    assert p.verdict(["crm", "user", "im"], 0, 0) == "нет права"
    assert "владельца" in " ".join(p.VERDICTS["нет права"])


def test_право_есть_и_ссылка_пришла_значит_правка_моя():
    p = probe()
    assert p.verdict(["crm", "disk"], 3, 0) == "право есть, путь мой"


def test_непрочитанные_права_не_читаются_как_тупик():
    """Пустой список прав — отказ измерения, а не отрицательный ответ."""
    p = probe()
    assert p.verdict([], 0, 0) == "не измерено"
    assert "отказ измерения" in " ".join(p.VERDICTS["не измерено"])


def test_обход_без_диска_предлагается_только_при_живой_ссылке():
    p = probe()
    assert p.verdict(["crm", "disk"], 0, 2) == "обход без диска"
    assert p.verdict(["crm", "disk"], 0, 0) == "тупик"


def test_выборка_берёт_только_непрочитанные_и_только_с_идентификатором():
    p = probe()
    doc = {"scopes": {
        "слово «Придуманное»": {"inventory": [
            {"file_id": "11", "download": "Диск не отдал ссылку (нет скоупа disk или файла нет)"},
            {"file_id": "12", "download": "ок"},
            {"file_id": "", "download": "Диск не отдал ссылку (нет скоупа disk или файла нет)"},
            {"file_id": "13", "download": "не требуется"},
        ]},
        "слово «Второе»": {"inventory": [
            {"file_id": "21", "download": "Диск не отдал ссылку (нет скоупа disk или файла нет)"},
        ]},
    }}
    got = [x["file_id"] for x in p.unread(doc)]
    assert got == ["11", "21"], got


def test_код_ошибки_вытаскивается_из_текста_исключения():
    p = probe()
    assert p.code_of("BitrixError: disk.file.get: ACCESS_DENIED Access denied") == "ACCESS_DENIED"
    assert p.code_of("") == "без ошибки"


def test_форма_объекта_это_только_имена_ключей():
    """Значения в журнал прогона не попадают: репозиторий публичный."""
    p = probe()
    got = p.shape({"id": 77, "name": "предложение.pdf", "urlMachine": "https://x/y?token=z"})
    assert got == ("id", "name", "urlMachine")
    assert all(v not in got for v in ("предложение.pdf", 77))
