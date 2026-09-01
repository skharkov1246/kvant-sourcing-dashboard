#!/usr/bin/env python3
"""Сборка gidromet/public/index.html — сайт заключения по гидрометаллургии.

Данные: gidromet/data/*.json. Плейсхолдеры в gidromet/site/index.template.html.
Файла может ещё не быть — тогда на его место идёт null, и вкладка честно
показывает «раздел собирается».

Запуск:  python3 gidromet/build.py
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA, SITE, OUT = ROOT / "data", ROOT / "site", ROOT / "public"

FILES = {
    "__PROJECT_JSON__": "project.json",
    "__REFRAME_JSON__": "reframe.json",
    "__DECISION_JSON__": "decision.json",
    "__SYRYE_JSON__": "syrye.json",
    "__PROVERKA_JSON__": "proverka.json",
    "__PIRROTIN_JSON__": "pirrotin.json",
    "__MARSHRUTY_JSON__": "marshruty.json",
    "__BALANSY_JSON__": "balansy.json",
    "__SEREBRO_JSON__": "serebro.json",
    "__SHEMA_JSON__": "shema.json",
    "__OBORUD_JSON__": "oborud.json",
    "__POSTAV_JSON__": "postav.json",
    "__CAPEX_JSON__": "capex.json",
    "__ECONOMY_JSON__": "economy.json",
    "__SCENARII_JSON__": "scenarii.json",
    "__NIC_JSON__": "nic.json",
    "__PROGRAMMA_JSON__": "programma.json",
    "__ETAPY_JSON__": "etapy.json",
    "__GONORAR_JSON__": "gonorar.json",
    "__VOPROSY_JSON__": "voprosy.json",
}


def compact(path: Path) -> str:
    """Проверяем JSON и вставляем компактно; </ нейтрализуем — данные едут в <script>."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build() -> None:
    tpl = (SITE / "index.template.html").read_text(encoding="utf-8")
    gotovo, v_rabote = [], []
    for key, name in FILES.items():
        assert key in tpl, f"нет плейсхолдера {key} в шаблоне"
        path = DATA / name
        if not path.exists():
            tpl = tpl.replace(key, "null")
            v_rabote.append(name)
            continue
        tpl = tpl.replace(key, compact(path))
        gotovo.append(name)
    tpl = tpl.replace("__BUILT_AT__", date.today().isoformat())
    assert "__" + "JSON__" not in tpl, "остались незаменённые плейсхолдеры"

    # деплой запрещает внешние ресурсы: страница обязана быть самодостаточной
    import re
    vneshnie = re.findall(r'(?:src|href)="https?://[^"]*"', tpl)
    assert not vneshnie, f"внешние ресурсы в странице: {vneshnie[:3]}"

    OUT.mkdir(exist_ok=True)
    out = OUT / "index.html"
    out.write_text(tpl, encoding="utf-8")
    print(f"OK -> {out} ({out.stat().st_size // 1024} КБ)")
    print(f"   разделов готово: {len(gotovo)} из {len(FILES)}")
    if v_rabote:
        print("   в работе (пока пусто):", ", ".join(v_rabote))


if __name__ == "__main__":
    build()
