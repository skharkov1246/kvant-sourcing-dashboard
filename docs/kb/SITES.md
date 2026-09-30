# Сайты портала

Закрытая страница: https://app.notion.com/p/3eb74b7ffc648124ad70df4840d66d31
Вход везде — Cloudflare Access (`access/cfaccess.js`), паролей на сайтах нет.

| сайт | адрес | проект Pages | сборка | деплой · данные |
|---|---|---|---|---|
| портал | https://kvant-sourcing-f122.pages.dev/ | kvant-sourcing-f122 | `main.py`, `public/_worker.js` | `deploy.yml` |
| дашборд | https://kvant-sourcing-f122.pages.dev/dashboard | kvant-sourcing-f122 | `dashboard.py` → `templates/dashboard_core.html` | `deploy.yml` |
| поставщики | https://kvant-sourcing-f122.pages.dev/suppliers | kvant-sourcing-f122 | `public/suppliers.html` | `deploy.yml` · `suppliers-publish.yml` (KV) |
| библиотека | https://kvant-sourcing-f122.pages.dev/library | kvant-sourcing-f122 | `public/library.html` | `deploy.yml` · `library-publish.yml` |
| номенклатура · бренды · счётчики | `/nomenclature` · `/brands` · `/counters` | kvant-sourcing-f122 | `public/*.html` | `deploy.yml` · `suppliers-quotes.yml`, `counters-publish.yml` (KV) |
| доступы и журнал | https://kvant-sourcing-f122.pages.dev/admin | kvant-sourcing-f122 | `access/acl.js` | `deploy.yml` |
| ГШО (ЗИП) | https://kvant-zip.pages.dev/ | kvant-zip | `zip/build.py` | `zip-deploy.yml` |
| ГТУ | https://kvant-zip.pages.dev/gt/ | kvant-zip | `gt/build.py` | `zip-deploy.yml` |
| ГПУ | https://kvant-gpu.pages.dev/ | kvant-gpu | `gpu/build.py` | `gpu-deploy.yml` |
| ОВЭ-75 | — | kvant-ove | `ove/build.py` | `ove-deploy.yml` |
| гидрометаллургия | — | kvant-gidromet | `gidromet/build.py` | `gidromet-deploy.yml` |
| ГОК | — | kvant-gok | страница самодостаточна | `factory-deploy.yml` |

Прогоны: https://github.com/skharkov1246/kvant-sourcing-dashboard/actions
