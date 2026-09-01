#!/usr/bin/env python3
"""Раскладка результатов расчётного потока по файлам gidromet/data/*.json.

Поток возвращает разделы без пометки, к какому файлу они относятся, поэтому
принадлежность определяется по содержанию. Признаки подобраны так, чтобы быть
редкими: величина или оборот, встречающийся практически только в своём разделе.
Назначение — жадное по убыванию уверенности на всей матрице сразу, а не по
одному результату, поэтому добавление новых результатов исправляет прежние ошибки.

Запуск:  python3 gidromet/tools/harvest.py [run_id]
"""
import json, re, sys
from pathlib import Path

WF = Path('/root/.claude/projects/-home-user-kvant-sourcing-dashboard/'
          '9fc27ead-3f1c-5371-9448-cfb44a38e144/subagents/workflows')
DATA = Path(__file__).resolve().parent.parent / 'data'

# редкие признаки: (регулярное выражение, вес). Вес — насколько признак характерен.
PRIZNAKI = {
    'project':   [(r'что покупает', 5), (r'387', 3), (r'389', 3), (r'сверк\w* масштаб', 5),
                  (r'три положени', 4), (r'1,9\s*%', 2)],
    'reframe':   [(r'284\s*млн', 6), (r'6,5\s*раз', 5), (r'два режима', 5),
                  (r'неверн\w* приоритет', 5), (r'4,3\s*раз', 3)],
    'decision':  [(r'входим', 6), (r'услови\w* входа', 6), (r'наши риски', 4),
                  (r'проектн\w* офис', 3), (r'выходим', 3)],
    'syrye':     [(r'39,8', 5), (r'34,1', 4), (r'18,1', 3), (r'кондици', 4),
                  (r'фазов\w* анализ', 3)],
    'proverka':  [(r'332,4', 8), (r'103,4', 6), (r'0,308', 5), (r'потерянн\w* разряд', 6),
                  (r'сходимост', 3)],
    'pirrotin':  [(r'4,83', 7), (r'8,9\s*кг/т', 6), (r'АМ-2Б', 5), (r'367\s?191', 6),
                  (r'11,14', 5)],
    'marshruty': [(r'"routes"', 40)],
    'balansy':   [(r'9\s?864', 7), (r'336\s*кг/т', 5), (r'известняк', 3),
                  (r'ксантогенат', 3), (r'тепловыделен', 4)],
    'serebro':   [(r'тиоцианат', 7), (r'аргентоярозит', 4), (r'элементн\w* сер', 4),
                  (r'известков\w* обработк', 4)],
    'shema':     [(r'компонов', 6), (r'обводнённост', 5), (r'генплан', 5),
                  (r'резервир', 4), (r'площадк', 2)],
    'oborud':    [(r'Бонд', 7), (r'403\s*м', 5), (r'т/ч', 3), (r'ZTMY', 5),
                  (r'установленн\w* мощност', 4)],
    'postav':    [(r'"blocks"', 40)],
    'capex':     [(r'594', 6), (r'431\s*млн', 6), (r'граница объ[её]м', 6),
                  (r'Ertis', 4), (r'Покровск', 3)],
    'economy':   [(r'9\s*[—-]\s*18\s*%', 7), (r'порог окупаемост', 6), (r'Карабашмед', 6),
                  (r'удержани\w* покупател', 5)],
    'scenarii':  [(r'2\s?920', 8), (r'5\s?602', 6), (r'безубыточн', 5), (r'чувствительност', 4)],
    'nic':       [(r'26-273', 8), (r'3,3\s*млн', 6), (r'40\s*рабоч', 5), (r'Фоменко', 5),
                  (r'Благовещенск', 4)],
    'programma': [(r'80,5', 7), (r'938', 6), (r'143\s*к\s*одному', 7),
                  (r'математическ\w* ожидани', 5), (r'оборотн\w* раствор', 3)],
    'etapy':     [(r'665\s*млн', 7), (r'накопленн', 5), (r'денежн\w* поток по годам', 6),
                  (r'финансируется потоком', 6), (r'2030\s*[—-]\s*2032', 4)],
    'gonorar':   [(r'82,2', 8), (r'база начислен', 6), (r'агентск', 4),
                  (r'маржа', 4), (r'вознагражден', 4)],
    'voprosy':   [(r'адресат', 7), (r'что меняется от ответа', 8), (r'срок ответа', 6)],
}


def ball(t, key):
    return sum(len(re.findall(p, t, re.I)) * w for p, w in PRIZNAKI[key])


def main(run: str) -> None:
    zhurnal = WF / run / 'journal.jsonl'
    if not zhurnal.exists():
        sys.exit(f'нет журнала {zhurnal}')
    rez = []
    for line in zhurnal.read_text(encoding='utf-8').splitlines():
        z = json.loads(line)
        if z.get('type') == 'result' and isinstance(z.get('result'), dict):
            rez.append(z['result'])
    print(f'результатов в журнале: {len(rez)}')
    if not rez:
        return

    teksty = [json.dumps(d, ensure_ascii=False) for d in rez]
    # матрица «результат × раздел», затем жадное назначение по убыванию уверенности
    pary = sorted(
        ((ball(teksty[i], k), i, k) for i in range(len(rez)) for k in PRIZNAKI),
        reverse=True)
    zanyat_rez, zanyat_kluch, naznach = set(), set(), {}
    for b, i, k in pary:
        if b <= 0 or i in zanyat_rez or k in zanyat_kluch:
            continue
        naznach[k] = (i, b)
        zanyat_rez.add(i); zanyat_kluch.add(k)

    DATA.mkdir(exist_ok=True)
    for k in sorted(naznach, key=lambda k: -naznach[k][1]):
        i, b = naznach[k]
        (DATA / f'{k}.json').write_text(
            json.dumps(rez[i], ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'  {k:<11} уверенность {b:>4}  <- {str(rez[i].get("lead",""))[:66]}')
    # файлы разделов, которые ещё не пришли, убираем, чтобы не показывать чужое содержание
    for p in DATA.glob('*.json'):
        if p.stem not in naznach:
            p.unlink()
            print(f'  {p.stem:<11} удалён (раздел ещё не собран)')
    ne_opoznano = [i for i in range(len(rez)) if i not in zanyat_rez]
    for i in ne_opoznano:
        print('  НЕ ОПОЗНАН:', str(rez[i].get('lead', ''))[:80])
    net = [k for k in PRIZNAKI if k not in naznach]
    if net:
        print('ещё нет:', ', '.join(net))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'wf_e768ef2f-9b6')
