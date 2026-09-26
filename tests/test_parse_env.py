"""Одна точка правды для окружения разбора: .github/actions/parse-env.

Распоряжение владельца 26.09.2026: «поменять ночной разбор так, чтобы он считал
так же, как ручной». До этого ночной разбор котировок и ежедневное пополнение шли
без ослабленной шапки и каскада, а ночной — и без читателей .doc, rar, 7z и PDF:
один и тот же файл ночью и руками разбирался по-разному.

Проверки держатся за поведение: действие ИСПОЛНЯЕТСЯ (его шаг флагов гоняется
bash-ем с теми входами, что передаёт каждый прогон), а полученное окружение
отдаётся индексатору — и спрашивается, что он из него понял. Номера строк и
порядок шагов внутри файлов не важны.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github" / "actions" / "parse-env" / "action.yml"
USES = "./.github/actions/parse-env"
#: Прогоны разбора: файл → задание.
ПРОГОНЫ = {
    "suppliers-quotes.yml": "quotes",     # ночной разбор котировок
    "library-daily.yml": "daily",         # ежедневное пополнение
    "library-index.yml": "index",         # ручной разбор и переразбор
}
#: Флаги, от которых зависит РЕЗУЛЬТАТ разбора, — они обязаны совпадать.
ФЛАГИ_РАЗБОРА = ("HEADER_RELAX", "CASCADE", "SPECGATE", "PDF_ONE_PASS")
#: Всё, что пишет действие, — ни один прогон не вправе задать это сам.
ВСЕ_ФЛАГИ = ФЛАГИ_РАЗБОРА + ("RETRY_FAILED", "RETRY_FAILED_LIMIT")
#: Запуск разбора в строке команды.
РАЗБОР = re.compile(r"library/(indexer|reparse|ocr|harvest_defects)\.py")


def _действие() -> dict:
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def _прогон(имя: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / имя).read_text(encoding="utf-8"))


def _входы_прогона(wf: dict) -> dict:
    on = wf.get("on") or wf.get(True) or {}
    return ((on.get("workflow_dispatch") or {}).get("inputs")) or {}


def _значение(выражение, входы: dict) -> str:
    """Значение параметра `with` при запуске с умолчаниями входов.

    Понимает литерал и ровно те выражения, что стоят в прогонах:
    `${{ inputs.x }}`, `${{ !inputs.x }}`, `${{ inputs.x && 'a' || 'b' }}`.
    Иное — провал: непонятое выражение значит непроверенный флаг."""
    if isinstance(выражение, bool):
        return "true" if выражение else "false"
    с = str(выражение).strip()
    м = re.fullmatch(r"\$\{\{\s*(!?)inputs\.(\w+)\s*(?:&&\s*'([^']*)'\s*\|\|\s*'([^']*)'\s*)?\}\}", с)
    if not м:
        assert "${{" not in с, f"непонятное выражение параметра: {с}"
        return с
    не, имя, да, нет = м.groups()
    умолч = входы[имя].get("default")
    if isinstance(умолч, str):
        умолч = {"true": True, "false": False}.get(умолч, умолч)
    if да is not None:
        return да if умолч else нет
    if не:
        return "false" if умолч else "true"
    return "true" if умолч is True else "false" if умолч is False else str(умолч)


def _шаг_действия(wf: dict, задание: str) -> tuple[int, dict]:
    шаги = wf["jobs"][задание]["steps"]
    свои = [(i, ш) for i, ш in enumerate(шаги) if ш.get("uses") == USES]
    assert len(свои) == 1, f"задание {задание}: действие parse-env вызвано {len(свои)} раз"
    return свои[0]


def _входы_действия(имя: str) -> dict[str, str]:
    """Входы действия, как их получает прогон при запуске с умолчаниями."""
    wf = _прогон(имя)
    _, шаг = _шаг_действия(wf, ПРОГОНЫ[имя])
    входы = {к: str(v.get("default", "")) for к, v in _действие()["inputs"].items()}
    for к, v in (шаг.get("with") or {}).items():
        assert к in входы, f"{имя}: у действия нет входа {к}"
        входы[к] = _значение(v, _входы_прогона(wf))
    return входы


def _исполнить_флаги(входы: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """Гоняет шаг флагов действия bash-ем и читает, что он записал в GITHUB_ENV."""
    шаг = next(ш for ш in _действие()["runs"]["steps"] if "GITHUB_ENV" in ш.get("run", ""))
    env = dict(os.environ)
    for к, шаблон in шаг["env"].items():
        м = re.fullmatch(r"\$\{\{\s*inputs\.(\w+)\s*\}\}", str(шаблон))
        assert м, f"значение {к} подставлено не входом: {шаблон}"
        env[к] = входы[м.group(1)]
    файл = tmp_path / "github_env"
    файл.write_text("", encoding="utf-8")
    env["GITHUB_ENV"] = str(файл)
    r = subprocess.run(["bash", "-c", шаг["run"]], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return dict(строка.split("=", 1) for строка in файл.read_text(encoding="utf-8").splitlines())


def _понял_индексатор(окружение: dict[str, str]) -> dict:
    """Что индексатор прочитал из окружения — читает ВЫЗОВ, а не объявление."""
    код = ("import json, indexer as i; print(json.dumps({'HEADER_RELAX': i.ШАПКА_ШИРЕ, "
           "'CASCADE': i.КАСКАД, 'SPECGATE': i.SPECGATE, 'PDF_ONE_PASS': i.ОДИН_ПРОХОД, "
           "'RETRY_FAILED': i.RETRY_FAILED, 'RETRY_FAILED_LIMIT': i.RETRY_FAILED_LIMIT}))")
    env = {к: v for к, v in os.environ.items() if к not in ВСЕ_ФЛАГИ}
    env.update(окружение)
    env["PYTHONPATH"] = str(ROOT / "library") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", код], env=env, capture_output=True, text=True,
                       cwd=ROOT)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("имя", sorted(ПРОГОНЫ))
def test_прогон_зовёт_одну_точку_до_разбора(имя):
    wf = _прогон(имя)
    шаги = wf["jobs"][ПРОГОНЫ[имя]]["steps"]
    i, _ = _шаг_действия(wf, ПРОГОНЫ[имя])
    разбор = [j for j, ш in enumerate(шаги) if РАЗБОР.search(ш.get("run", ""))]
    assert разбор, f"{имя}: шага разбора не найдено"
    assert i < min(разбор), f"{имя}: окружение разбора ставится после разбора"


@pytest.mark.parametrize("имя", sorted(ПРОГОНЫ))
def test_прогон_не_ставит_пакеты_и_не_задаёт_флаги_сам(имя):
    """Своя установка или своя переменная — это второй источник правды.

    Переменная в env шага или задания перекрывает окружение, записанное
    действием, и прогон молча считал бы по-своему."""
    wf = _прогон(имя)
    задание = wf["jobs"][ПРОГОНЫ[имя]]
    окружения = [wf.get("env") or {}, задание.get("env") or {}]
    окружения += [ш.get("env") or {} for ш in задание["steps"]]
    for env in окружения:
        лишние = set(env) & set(ВСЕ_ФЛАГИ)
        assert not лишние, f"{имя}: флаг задан мимо parse-env: {sorted(лишние)}"
    for ш in задание["steps"]:
        run = "\n".join(s.split("#")[0] for s in ш.get("run", "").splitlines())
        assert "apt-get install" not in run, f"{имя}: свои системные пакеты в шаге {ш.get('name')}"
        assert "pip install" not in run, f"{имя}: свои пакеты Python в шаге {ш.get('name')}"
        for флаг in ВСЕ_ФЛАГИ:
            assert not re.search(rf"\b{флаг}=", run), f"{имя}: {флаг} прибит в строке запуска"


def test_ночной_ежедневный_и_ручной_считают_одинаково(tmp_path):
    """Флаги разбора при запуске с умолчаниями совпадают у всех трёх прогонов,
    и индексатор понимает их как «ослабленная шапка и каскад включены»."""
    поняли = {}
    for имя in ПРОГОНЫ:
        окружение = _исполнить_флаги(_входы_действия(имя), tmp_path)
        assert set(ВСЕ_ФЛАГИ) <= set(окружение), f"{имя}: действие записало не все флаги"
        поняли[имя] = _понял_индексатор(окружение)
    разбор = {имя: {ф: п[ф] for ф in ФЛАГИ_РАЗБОРА} for имя, п in поняли.items()}
    assert len({json.dumps(v, sort_keys=True) for v in разбор.values()}) == 1, разбор
    for имя, п in поняли.items():
        assert п["HEADER_RELAX"] is True, f"{имя}: ослабленная шапка выключена"
        assert п["CASCADE"] is True, f"{имя}: каскад выключен"
        assert п["SPECGATE"] is True, f"{имя}: ворота спецификации выключены"


@pytest.mark.parametrize("имя", ["suppliers-quotes.yml", "library-daily.yml"])
def test_сам_идущий_прогон_повторяет_не_скачавшиеся_с_пределом(имя, tmp_path):
    """Ночью и ежедневно повтор включён, но с пределом: нагрузка на портал
    обязана быть посчитана (CLAUDE.md, «Битрикс не перегружать», п. 5)."""
    п = _понял_индексатор(_исполнить_флаги(_входы_действия(имя), tmp_path))
    assert п["RETRY_FAILED"] is True
    assert 0 < п["RETRY_FAILED_LIMIT"] <= 500, "повтор без предела — нагрузка не посчитана"


def test_выключенный_вход_ручного_прогона_доезжает_нулём(tmp_path):
    """Замер A/B: выключенный вход обязан выключать, а не молча оставлять умолчание."""
    wf = _прогон("library-index.yml")
    _, шаг = _шаг_действия(wf, "index")
    входы = {к: dict(v) for к, v in _входы_прогона(wf).items()}
    for к in ("header_relax", "cascade", "specgate"):
        входы[к]["default"] = False
    значения = {к: str(v.get("default", "")) for к, v in _действие()["inputs"].items()}
    for к, v in шаг["with"].items():
        значения[к] = _значение(v, входы)
    п = _понял_индексатор(_исполнить_флаги(значения, tmp_path))
    assert п["HEADER_RELAX"] is False and п["CASCADE"] is False and п["SPECGATE"] is False


def test_опечатка_во_флаге_роняет_шаг(tmp_path):
    """«yes» вместо «1» не должно молча превращаться в «выключено»."""
    входы = {к: str(v.get("default", "")) for к, v in _действие()["inputs"].items()}
    входы["cascade"] = "yes"
    with pytest.raises(AssertionError):
        _исполнить_флаги(входы, tmp_path)


def test_действие_ставит_читателей_форматов_и_распознавание():
    шаги = _действие()["runs"]["steps"]
    run = "\n".join(ш.get("run", "") for ш in шаги)
    for пакет in ("antiword", "catdoc", "p7zip-full", "unrar", "libarchive-tools",
                  "poppler-utils", "qpdf", "pyxlsb", "openpyxl", "xlrd", "pypdf",
                  "libreoffice-calc-nogui", "libreoffice-writer-nogui",
                  "tesseract-ocr", "tesseract-ocr-rus"):
        assert пакет in run, f"{пакет} не ставится"
    # Ежедневному нужно распознавание — он обязан его попросить.
    assert _входы_действия("library-daily.yml")["ocr"] == "true"


# ─────────────────────────────── очередь повтора «не скачался»
def test_новые_файлы_идут_все_а_повторы_с_пределом_от_давних():
    sys.path.insert(0, str(ROOT / "library"))
    import indexer
    mine = [{"file_id": к} for к in ("н1", "п3", "н2", "п1", "п2")]
    очередь = {"п1": 0, "п2": 1, "п3": 2}      # 0 — давняя попытка
    список, взято, отложено = indexer.отобрать_повторы(mine, очередь, 2)
    assert [r["file_id"] for r in список] == ["н1", "н2", "п1", "п2"]
    assert (взято, отложено) == (2, 1)
    список, взято, отложено = indexer.отобрать_повторы(mine, очередь, 0)
    assert len(список) == 5 and (взято, отложено) == (3, 0), "0 — без предела"
