#!/usr/bin/env python3
"""Раскладка результатов расчётного потока по файлам gidromet/data/*.json.

Поток возвращает разделы без пометки, к какому файлу они относятся, поэтому
принадлежность определяется по содержанию: у каждого раздела свой набор
характерных слов. Совпадение печатается, чтобы его можно было проверить глазами.

Запуск:  python3 gidromet/tools/harvest.py
"""
import json, re, sys
from pathlib import Path

WF = Path('/root/.claude/projects/-home-user-kvant-sourcing-dashboard/'
          '9fc27ead-3f1c-5371-9448-cfb44a38e144/subagents/workflows')
DATA = Path(__file__).resolve().parent.parent / 'data'

# характерные слова каждого раздела; вес — длина слова, чтобы редкие термины весили больше
PRIZNAKI = {
    'project':   ['главное', 'три положения', 'что покупает', 'сверк', 'нашли'],
    'reframe':   ['постановк', 'приоритет', 'два режима', 'доля золота в стоимости', 'неверный приоритет'],
    'decision':  ['входим', 'условия входа', 'наши риски', 'рекомендация', 'проектный офис'],
    'syrye':     ['минеральн', 'халькопирит', 'пирротин', 'качеств', 'кондици'],
    'proverka':  ['сходимост', 'висмут', 'аргентоярозит' , 'баланс', 'не бьются'],
    'pirrotin':  ['пирротинов', 'цианирован', 'ам-2б', 'нитрат свинца', 'второй продукт'],
    'marshruty': ['маршрут', 'сравнение маршрутов', 'albion', 'атмосферн'],
    'balansy':   ['тепловыделен', 'кислород', 'серн', 'реагент', 'известняк'],
    'serebro':   ['серебр', 'аргентоярозит', 'тиоцианат', 'элементн'],
    'shema':     ['компонов', 'площадк', 'операц', 'очерёдн', 'генплан'],
    'oborud':    ['бонд', 'мельниц', 'камер', 'т/ч', 'установленн'],
    'postav':    ['поставщик', 'экстрагент', 'фурм', 'уралмаш', 'китай'],
    'capex':     ['капитал', 'граница объёма', 'перерасход', 'амурск', 'резерв'],
    'economy':   ['продаж концентрата', 'порог окупаемост', 'дисконт', 'удержан', 'карабашмед'],
    'scenarii':  ['цена золота', 'безубыточн', 'чувствительност', 'сценар', 'вскрыш'],
    'nic':       ['ниц', '26-273', 'этап 1', 'плюс-минус 50', 'покровск'],
    'programma': ['программа испытан', 'оборотн', 'укрупнённ', 'математическое ожидан', 'регламент'],
    'etapy':     ['очеред', 'этапност', 'денежный поток', 'накопленн', 'ввод'],
    'gonorar':   ['вознагражден', 'агентск', 'epcm', 'база начислен', 'маржа'],
    'voprosy':   ['вопрос', 'адресат', 'что меняется', 'заказчику', 'срок ответа'],
}


def sobrat(d):
    """Весь текст раздела одной строкой в нижнем регистре."""
    return json.dumps(d, ensure_ascii=False).lower()


def opredelit(d, zanyato):
    if 'routes' in d:
        return 'marshruty'
    if 'blocks' in d:
        return 'postav'
    t = sobrat(d)
    ball = {}
    for k, slova in PRIZNAKI.items():
        if k in zanyato:
            continue
        ball[k] = sum(t.count(s) * len(s) for s in slova)
    if not ball:
        return None
    luchshiy = max(ball, key=ball.get)
    return luchshiy if ball[luchshiy] > 0 else None


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
    DATA.mkdir(exist_ok=True)
    zanyato, ulozheno = set(), 0
    # сначала однозначные, потом остальные по убыванию уверенности
    for d in rez:
        k = opredelit(d, zanyato)
        if not k:
            print('  НЕ ОПОЗНАН раздел:', str(d.get('lead', ''))[:90])
            continue
        (DATA / f'{k}.json').write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
        zanyato.add(k)
        ulozheno += 1
        print(f'  {k:<11} <- {str(d.get("lead",""))[:78]}')
    print(f'уложено файлов: {ulozheno}')
    net = [k for k in PRIZNAKI if k not in zanyato]
    if net:
        print('ещё нет:', ', '.join(net))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'wf_e768ef2f-9b6')
