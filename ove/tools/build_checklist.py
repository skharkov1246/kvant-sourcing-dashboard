#!/usr/bin/env python3
"""Чек-лист владельца перед подачей (P-14) → ove/public/docs/apply/ove75-checklist-podachi.pdf.

Источник пунктов — редакторская сверка пакета подачи (три аудита, 27.08.2026).
Печать: headless Chromium (Playwright), A4. Запускается вручную:
    .venv/bin/python ove/tools/build_checklist.py
В сборку build.py не включён (браузер в CI не нужен — файл статичен).
"""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "docs" / "apply" / "ove75-checklist-podachi.pdf"
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

ITEMS = [
    ("Состав архива подачи",
     "Архив собирается строго из P-01–P-05 + юрпакет (P-11/P-12) + формы организатора. "
     "Рабочие документы партнёрской линии P-06/P-07 (папка docs/partner/ — именуют партнёра), "
     "расчётная записка и ПЗ лотов в архив НЕ кладутся."),
    ("Автопоиск по всем вложениям",
     "Контекстный поиск по каждому файлу архива: наименование партнёра (EN/RU варианты), "
     "«санкц», «calc.json», «вкладк», «владельц», внутренние шифры C-/D-/E-/Q-/F-/M-/NB- — "
     "ноль вхождений."),
    ("Служебные плашки и бланк",
     "Серые строки «ЭТУ СТРОКУ УДАЛИТЬ» удалены из всех четырёх DOCX; письмо — на бланке, "
     "с подписью и печатью."),
    ("Плейсхолдеры и арифметика КП",
     "Все поля [___] заполнены; в КП нет красного текста; Этап 1 + Этап 2 = Итого; "
     "сумма лотов + ЦИМ = ИТОГО; «Итого с НДС» согласовано с выбранной ставкой."),
    ("Синхронизация дат",
     "Дата письма = даты шапок ТП и квалификационной справки; срок действия заявки = "
     "сроку действия КП (не менее требуемого документацией)."),
    ("Названия лотов и титулы",
     "Названия лотов в письме, ТП, КП и ПЗ — в одной редакции (по дословной формулировке ТЗ); "
     "строка приложения 4 письма дословно совпадает с титулом PDF предложений и вопросов."),
    ("Этапность",
     "Везде одинаково: «предлагаем зафиксировать: Этап 1 — 3 месяца, Этап 2 — ещё 3, всего "
     "не более 6»; условие начала Этапа 1 сформулировано одинаково в ТП и КП."),
    ("Опись против содержимого",
     "Опись приложений письма сверена с фактическим содержимым архива поштучно: номера, "
     "названия, количество листов; ничего лишнего и ничего потерянного."),
    ("Расчётная записка (для защиты)",
     "Шапка — к ТЗ v4.1; инженерная рецензия пройдена (2–3 дня), Ф.И.О. и дата рецензента "
     "вписаны в преамбулу; контрольный автопоиск шифров и внутренних ссылок — ноль вхождений."),
    ("ПЗ лотов (для защиты)",
     "Базис «новое строительство» — первым абзацем каждой ПЗ; вставки резервного сценария А "
     "помечены; контрольный автопоиск шифров и внутренних ссылок — ноль вхождений."),
    ("Формы организатора",
     "Если с извещением пришли формы — КП и декларации перенесены в формы организатора, "
     "опись письма скорректирована."),
    ("Действительность справок",
     "Справки P-12 действительны на дату подачи; выписка СРО свежая (либо решение по "
     "привлечению члена СРО оформлено)."),
    ("Реквизиты подачи",
     "Адресат, способ и срок подачи — из извещения; подтверждение получения пакета "
     "организатором зафиксировать."),
    ("Финальное контрольное чтение",
     "Письмо и первая страница ТП: партнёр не назван, тон уважительный, без канцелярита "
     "и заискивания; полномочия подписанта приложены."),
]


def html() -> str:
    rows = "\n".join(
        f'<li><span class="box"></span><div><b>{t}.</b> {d}</div></li>'
        for t, d in ITEMS)
    today = date.today().strftime("%d.%m.%Y")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 20mm 15mm 18mm 15mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "PT Sans", "Segoe UI", Arial, sans-serif; color: #1a1a1a;
         font-size: 10.5pt; line-height: 1.45; margin: 0; }}
  .brand {{ color: #1F3864; font-weight: 700; font-size: 15pt; }}
  .req {{ color: #595959; font-size: 8pt; border-bottom: 2.2px solid #1F3864;
          padding: 2px 0 6px; margin-bottom: 18px; }}
  h1 {{ color: #1F3864; font-size: 14.5pt; text-align: center; margin: 0 0 4px; }}
  .sub {{ color: #595959; text-align: center; font-size: 9.5pt; margin: 0 0 14px; }}
  ol {{ margin: 0; padding: 0; counter-reset: n; list-style: none; }}
  li {{ display: flex; gap: 8px; padding: 6px 0 6px; border-bottom: 1px solid #E3E6EC;
        counter-increment: n; break-inside: avoid; }}
  li > div::before {{ content: counter(n) ". "; font-weight: 700; color: #1F3864; }}
  .box {{ flex: 0 0 auto; width: 11px; height: 11px; border: 1.4px solid #1F3864;
          border-radius: 2px; margin-top: 3px; }}
  .note {{ margin-top: 14px; color: #595959; font-size: 9pt; }}
  .sign {{ margin-top: 22px; font-size: 10.5pt; }}
</style></head><body>
  <div class="brand">ООО «КВАНТ»</div>
  <div class="req">[ИНН/КПП/ОГРН, юридический адрес, телефон, e-mail — реквизиты компании]</div>
  <h1>Чек-лист владельца перед подачей заявки</h1>
  <p class="sub">Конкурсная процедура Базового инжиниринга КГМК.ОВЭ-75 · к реестру пакета подачи
     (позиции P-01…P-14) · составлен {today}</p>
  <ol>{rows}</ol>
  <p class="note">Чек-лист — внутренний контрольный документ ООО «КВАНТ»; в архив подачи
     не вкладывается. Каждый пункт отмечается после фактической проверки.</p>
  <p class="sign">Проверил: ____________________ / [Ф.И.О.] /&nbsp;&nbsp;&nbsp;дата: ____________</p>
</body></html>"""


def build() -> Path:
    from playwright.sync_api import sync_playwright
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME)
        pg = br.new_page()
        pg.set_content(html(), wait_until="load")
        pg.pdf(path=str(OUT), prefer_css_page_size=True, print_background=True)
        br.close()
    print(f"PDF → {OUT} ({OUT.stat().st_size // 1024} КБ)")
    return OUT


if __name__ == "__main__":
    build()
