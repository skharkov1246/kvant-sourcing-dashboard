# База ЗИП — снабжение / прайс / ODM

Внутренний веб-инструмент по ЗИП подземной горной техники (перфораторы Epiroc/Atlas Copco,
Sandvik Tamrock, Caterpillar, Normet). По каждой позиции — китайские **ODM-производители**
для импортозамещения, статусы поставщиков, **цены конкурентов / таможенная статистика**,
**чертежи**, и связка с Bitrix. Живёт на Cloudflare Pages: **https://kvant-zip.pages.dev**
(проект `kvant-zip`). Данные — в Supabase; сайт офлайн-фолбэком читает вшитый снимок (SEED).

Этот каталог — восстановленный из задеплоенного сайта исходник (прошлая сессия деплоила
напрямую через wrangler, в git код не попадал). Теперь всё под контролем git.

## Структура

```
zip/
  data/positions.json       752 позиции (снимок для SEED/офлайна)
  data/odm_suppliers.json   ~4.3k ODM-записей китайских заводов
  data/price_records.json   цены конкурентов / таможня по годам (наполняется)
  site/index.template.html  исходник сайта; SEED вставляется в плейсхолдер [/*__SEED__*/]
  site/vendor/supabase.js   самохостинг supabase-js v2 (без CDN)
  site/_worker.js.example   опциональный пароль-гейт (по умолчанию сайт открыт)
  supabase/migrations.sql   таблицы price_records, drawings + Storage-бакет + RLS
  build.py                  сборка zip/public/index.html из шаблона и данных
```
`zip/public/` — артефакт сборки, в git не хранится (`.gitignore`).

## Сборка и локальный просмотр

```bash
python zip/build.py            # → zip/public/index.html (+ vendor/)
# открыть zip/public/index.html в браузере: работает офлайн на SEED,
# в режиме «● офлайн-копия» правки/загрузки не сохраняются (нужен Supabase).
```

## Данные и БД (Supabase)

Проект Supabase: `vpjliavuuxjcvtxbthlp`. URL и **publishable**-ключ вшиты в сайт (это
не секрет — ключ для анонимного клиента). Таблицы:
- `positions`, `odm_suppliers`, `rfq_requests`, `change_log` — были ранее;
- `price_records`, `drawings` + Storage-бакет `drawings` — добавляет `supabase/migrations.sql`.

**Применить миграции** (один раз):
- GitHub → Actions → **ZIP base — apply DB migrations** → Run workflow.
  Нужен секрет репозитория `SUPABASE_DB_URL` (Supabase → Settings → Database → Connection
  string, URI). Идемпотентно.
- **Или** вручную: Supabase → SQL Editor → вставить `supabase/migrations.sql` → Run.

## Деплой

Автоматически: push в `main`, затрагивающий `zip/**`, запускает workflow
**ZIP base deploy** → `build.py` → `wrangler pages deploy zip/public --project-name=kvant-zip`.
Использует те же секреты, что дашборд: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
Ручной запуск — Actions → ZIP base deploy → Run workflow.

## Возможности сайта

- Таблица 752 позиций: поиск, фильтры (категория/OEM/Bitrix/с ODM/без ODM), сортировка.
- Разворот позиции: применяемость и кросс-номера, упоминания в сделках Bitrix, карточки
  ODM-заводов (статус/качество/коммент/RFQ), **блок цен конкурентов/таможни** (добавление
  и правка строк), **блок чертежей** (загрузка/скачивание файлов через Supabase Storage).
- Вкладки: Поставщики (группировка заводов под один RFQ на пакет позиций), Сводка
  (покрытие ODM и ценами по категориям), Журнал изменений.
- Экспорт в Excel/CSV: позиции, ODM, цены, поставщики.
- Онлайн-режим (`● онлайн`) — правки пишутся в Supabase для всей команды; офлайн-копия —
  только просмотр вшитого снимка.

## Безопасность

Сайт открыт (без логина) — по решению владельца. Включить пароль: скопировать
`site/_worker.js.example` → `zip/public/_worker.js` и задать `BASIC_AUTH_PASS` в Pages.
Запись в БД идёт по anon-ключу — чувствительные операции при необходимости сузить через
RLS в `supabase/migrations.sql`.

## Обновление снимка (SEED)

Снимок в `data/*.json` — офлайн-фолбэк. Освежить из живой БД можно экспортом из Supabase
(или кнопками «⬇ Excel» на сайте) с последующей конвертацией в JSON того же формата и
пересборкой. Боевой сайт всегда показывает актуальные данные напрямую из Supabase.
