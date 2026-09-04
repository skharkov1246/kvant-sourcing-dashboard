"""Обход установки: десятки снятых деталей собираются в одно дерево.

Сессия по построению описывает ОДНУ деталь — это правильно для склада, где
позиции приходят поштучно. Но на объекте бывает доступ к машине целиком:
кладовщик обходит турбину и снимает шильдик установки, потом узлы, потом
детали внутри узлов. Каждая съёмка остаётся отдельной сессией (иначе
прерванный обход терял бы всё), а связывает их КЛЮЧ УСТАНОВКИ — бренд,
обозначение и заводской номер с её шильдика.

Сервер складывает из этих сессий дерево «установка → узлы → детали» и,
главное, честно говорит, чего в нём НЕ ХВАТАЕТ: узел без единой детали,
деталь без узла, отсутствующий шильдик установки. Обход — работа на часы,
и человек должен видеть, что ещё не снято, ПОКА он у машины, а не через
неделю от конструктора.
"""

from __future__ import annotations

from typing import Any

from .brand_chain import LEVEL_TITLES, normalize_article


def _node_of(nodes: list[dict[str, Any]], level: str) -> dict[str, Any] | None:
    return next((n for n in nodes if n.get("level") == level), None)


def assembly_key(node: dict[str, Any] | None) -> str:
    """Ключ узла внутри установки: обозначение и позиция.

    Позиция значима: два одинаковых насоса в одной турбине — разные узлы,
    их путают чаще всего («мы же снимали такой») и заказывают одну деталь
    вместо двух.
    """
    if node is None:
        return ""
    parts = [normalize_article(p) for p in
             (node.get("brand"), node.get("designation"), node.get("position")) if p]
    return "|".join(p for p in parts if p)


def build_tree(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Дерево обхода из плоского списка снятых деталей.

    На вход — карточки с их цепочками установки (item_id, nodes, title).
    На выход — установка, её узлы и детали в каждом, плюс детали, снятые
    без узла: они не выбрасываются и не приписываются к случайному узлу —
    висят отдельной группой «узел не указан», это работа для человека.
    """
    equipment: dict[str, Any] | None = None
    assemblies: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []

    for item in items:
        nodes = item.get("installation") or []
        equipment = equipment or _node_of(nodes, "equipment")
        part = _node_of(nodes, "part") or {}
        entry = {
            "item_id": item["item_id"],
            "brand": part.get("brand"),
            "designation": part.get("designation"),
            "position": part.get("position"),
            # Пути закупки уже посчитаны для карточки — здесь только счётчик,
            # чтобы дерево не превратилось в простыню.
            "procurement_paths": len(item.get("procurement_paths") or []),
        }
        node = _node_of(nodes, "assembly")
        key = assembly_key(node)
        if not key:
            orphans.append(entry)
            continue
        bucket = assemblies.setdefault(key, {
            "key": key,
            "brand": (node or {}).get("brand"),
            "designation": (node or {}).get("designation"),
            "position": (node or {}).get("position"),
            "parts": [],
        })
        bucket["parts"].append(entry)

    return {
        "equipment": equipment,
        "assemblies": sorted(assemblies.values(), key=lambda a: a["key"]),
        "parts_without_assembly": orphans,
        "counts": {
            "assemblies": len(assemblies),
            "parts": sum(len(a["parts"]) for a in assemblies.values()) + len(orphans),
        },
    }


def gaps(tree: dict[str, Any]) -> list[str]:
    """Чего в обходе не хватает — списком для человека, ПОКА он у машины."""
    out: list[str] = []
    equipment = tree.get("equipment") or {}
    if not equipment:
        out.append("нет шильдика установки — снимите его, иначе детали не "
                   "соберутся в один обход")
    elif not equipment.get("serial"):
        out.append("у установки нет заводского номера — две одинаковые машины "
                   "на площадке сольются в одну")

    for assembly in tree.get("assemblies", []):
        if not assembly["parts"]:
            out.append(f"узел «{assembly.get('designation') or assembly['key']}» "
                       f"без единой детали")
        if not assembly.get("position"):
            out.append(f"у узла «{assembly.get('designation') or assembly['key']}» "
                       f"нет позиции — два одинаковых узла станут одним")

    orphans = tree.get("parts_without_assembly") or []
    if orphans:
        out.append(f"деталей без узла: {len(orphans)} — укажите, в чём они стоят")
    if not tree.get("assemblies") and not orphans:
        out.append("в обходе нет ни одной детали")
    return out
