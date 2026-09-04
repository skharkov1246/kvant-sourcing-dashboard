# CLAUDE.md — правила работы над проектом

Этот файл читают агенты Claude Code, работающие в репозитории. Соблюдай его.

## Что это за проект
**Sourcing Analyzer (КВАНТ × Bitrix24)** — внутренний дашборд анализа работы сорсеров.
По живым данным Bitrix24 (смарт-процесс «Запросы поставщикам», entityTypeId 166) считает
метрики, генерирует HTML-дашборд и деплоит его на Cloudflare Pages за Basic Auth.

- Точка входа: `main.py` → тянет данные из Bitrix → модули метрик → рендер HTML.
- Модули расчёта: `metrics.py`, `company.py`, `advisor.py`, `reps.py`, `kam.py`,
  `contracts.py`, `insights.py`, `stages.py`, `period.py`.
- Вёрстка: **`templates/dashboard_core.html`** — один самодостаточный HTML-шаблон
  (встроенные CSS+JS, данные подставляются плейсхолдерами `__..._JSON__`). 9 вкладок.
- Сборка HTML: `dashboard.py`.

## 🔴 Критические правила (нарушение = сломанный прод)

1. **НИКОГДА не коммить и не пушь напрямую в `main`.** Только: своя ветка → Pull Request →
   мерж. Ветка: `git checkout -b feature/<короткое-описание>`.
   **Мерж PR агент выполняет сам** (распоряжение владельца, 06.08.2026: «мерджи всегда сам») —
   но ТОЛЬКО после зелёного гейта (`.github/workflows/gate.yml`).
2. **Каждый мерж в `main` = деплой на живой сайт.** Репозиторий публичный, минуты Actions
   не тарифицируются, но батчить изменения в один PR всё равно правильно: меньше
   пересборок — меньше окон, в которых прод может оказаться сломанным.
3. **Проверяй ДО пуша.** Обязательный минимум перед PR:
   ```bash
   python -m pytest -q                       # тесты ядра (~0,2 с)
   ruff check .                              # ошибки кода
   python scripts/build_catalog.py --check   # каталог данных актуален
   ```
   Правки вёрстки — дополнительно smoke-рендер и валидация:
   ```bash
   python -c "import sys,types;sys.modules.setdefault('dotenv',types.SimpleNamespace(load_dotenv=lambda *a,**k:None));sys.path.insert(0,'.');import dashboard;from tests import fixture;dashboard.write(fixture.build_metrics(),{'source':'rules','items':[]},'smoke/index.html')"
   python scripts/validate_dashboard.py smoke/index.html
   ```
   Правки метрик — `python main.py --dry-run` (нужен `BITRIX_WEBHOOK_URL` в `.env`).
   Ровно эти проверки выполняет гейт на каждый PR.
4. **Не редактируй бот-файлы.** `data/chat_snapshot.json`, `gt/data/bitrix_gt.json`,
   `gt/public/` пишут workflow. Если они попали в дифф PR — верни их состоянием
   базовой ветки: `git checkout origin/main -- <файл>`. Иначе мерж откатит свежие
   данные и дашборд соберётся на устаревших (так уже случалось трижды в августе).
5. **Репозиторий публичный.** Не добавляй персональные, коммерческие и клиентские
   данные. Что уже открыто и что с этим делать — `SECURITY.md`.
6. **Новый набор данных → в каталог.** Пояснение в `data/catalog_notes.json`,
   затем `python scripts/build_catalog.py`.

## 🧭 Начало сессии

1. Прочитай `.agent/manifest.json` — машинное описание репозитория: подпроекты,
   команды, расписания, бот-файлы, запретные зоны.
2. `data/catalog.json` — что за данные есть, свежие ли, насколько чувствительные.
3. Проверь хвосты: `git ls-remote --heads origin` — если висят старые `claude/*`
   ветки с невлитой работой, скажи об этом владельцу.

## Локальный запуск
```bash
python3 -m venv .venv
.venv/bin/pip install anthropic requests pydantic python-dotenv
# для реальных данных — создать .env с BITRIX_WEBHOOK_URL=https://<портал>.bitrix24.ru/rest/<id>/<токен>/
.venv/bin/python main.py --period 2026-05 --dry-run     # метрики без деплоя
```
Для правок только вёрстки данные Bitrix не нужны — рендерь шаблон на моковых данных.

## Стиль работы
- Коммиты — понятными сообщениями на русском, по-смыслу (что и зачем).
- Правки вёрстки держи в стиле окружающего кода (плотный inline CSS/JS шаблона).
- После нетривиальной правки — прогони визуальную проверку в браузере перед PR.
- Если правка затрагивает деплой/планировщик/секреты — сначала спроси владельца.
