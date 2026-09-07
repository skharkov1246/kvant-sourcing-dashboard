# Старт за 60 секунд

Файл для того, кто открыл репозиторий впервые — человека или агента.
Машинная версия того же: `.agent/manifest.json`.

## Что это

Монорепозиторий инструментов КВАНТ. Внутри семь независимых продуктов:

| Что | Где | Сборка | Сайт |
|---|---|---|---|
| Дашборд сорсеров (Bitrix24) | корень | `main.py` | kvant-sourcing-f122, за паролем |
| ОВЭ-75 | `ove/` | `ove/build.py` | kvant-ove, за паролем |
| ГТУ-библиотека | `gt/` | `gt/build.py` | внутри kvant-zip |
| База ЗИП | `zip/` | `zip/build.py` | kvant-zip, открыт |
| ГПУ-библиотека | `gpu/` | `gpu/build.py` | kvant-gpu, за паролем |
| Гидрометаллургия | `gidromet/` | `gidromet/build.py` | kvant-gidromet, открыт |
| Базовый проект ГОКа | `factory/` | без сборки | kvant-gok, открыт |

## Где данные

`data/catalog.json` — каталог всех 158 наборов данных: путь, объём, число записей,
ключи, кто и когда обновлял, какие файлы читают, оценка чувствительности.
Пересобрать: `python scripts/build_catalog.py`.

Оттуда же видно, что 43 набора помечены как конфиденциальные — это контакты
поставщиков, бюджеты сделок, таможенные декларации и метрики сотрудников.

## Проверки перед PR

```bash
python -m pytest -q                          # тесты ядра, ~0,2 с
ruff check .                                 # ошибки кода
python scripts/build_catalog.py --check      # каталог данных актуален
```

Правка вёрстки дашборда — обязательно рендер и валидация:

```bash
python -c "import sys,types;sys.modules.setdefault('dotenv',types.SimpleNamespace(load_dotenv=lambda *a,**k:None));\
sys.path.insert(0,'.');import dashboard;from tests import fixture;\
dashboard.write(fixture.build_metrics(),{'source':'rules','items':[]},'smoke/index.html')"
python scripts/validate_dashboard.py smoke/index.html
```

Правка логики метрик — `python main.py --dry-run` (нужен `BITRIX_WEBHOOK_URL` в `.env`).

То же самое автоматически выполняет `.github/workflows/gate.yml` на каждый PR.

## Чего не делать

- Не коммитить в `main` напрямую — только ветка и PR.
- Не редактировать бот-файлы (`data/chat_snapshot.json`, `gt/data/bitrix_gt.json`):
  мерж такого PR откатит свежие данные, и дашборд соберётся на устаревших.
- Не трогать `.github/workflows/`, `scheduler/`, `public/_worker.js` и секреты
  без отдельного согласования владельца.
- Не добавлять в репозиторий новые персональные и коммерческие данные:
  он публичный.

## Второй репозиторий

`skharkov1246/ocm-research-diary` — открытый дневник квантово-химических расчётов
(MatterForge). Устроен так же: `.agent/manifest.json` и `data/catalog.json` с той же
схемой, поэтому оба репозитория читаются одним и тем же способом.
