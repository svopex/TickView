"""Řazení symbolů ve watchlistu (záhlaví sloupce Symbol jako v TradingView).

Modul obsahuje jen čisté funkce – načtení seznamu a uložení volby řeší UI.
"""

from tickview.i18n import N_

# Řazení: klíč -> popis ("" = pořadí přidání, asc / desc podle abecedy)
WATCHLIST_SORTS: dict[str, str] = {"": N_("Pořadí přidání"), "asc": "A→Z", "desc": "Z→A"}


def sort_watchlist(items: list[dict], sort: str) -> list[dict]:
    """Symboly seznamu v požadovaném pořadí.

    Abeceda nerozlišuje velikost písmen; shodný symbol z více zdrojů se řadí
    podle zdroje (u Z→A také vzestupně). Neznámé řazení = pořadí přidání.

    Parametry:
        items: symboly [{"source", "symbol"}] v pořadí přidání.
        sort: klíč WATCHLIST_SORTS.
    """
    if sort not in ("asc", "desc"):
        return list(items)
    # Nejdřív podle zdroje, pak stabilně podle symbolu – zdroj zůstane vzestupně i u Z→A
    by_source = sorted(items, key=lambda item: item["source"])
    return sorted(by_source, key=lambda item: item["symbol"].casefold(), reverse=sort == "desc")
