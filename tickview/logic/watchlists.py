"""Řazení symbolů ve watchlistu, vlaječky a sekce seznamu (jako v TradingView).

Modul obsahuje jen čisté funkce – načtení seznamu a uložení volby řeší UI.

Sekce se drží jako skupiny [(section_id, [klíče symbolů])] v pořadí seznamu;
právě jedna skupina má section_id None (symboly bez sekce). Ta může stát
kdekoli mezi sekcemi – nahoře se zobrazuje bez záhlaví (jako v TradingView),
jinde se záhlavím „Bez sekce“; prázdná je skrytá a drží se na začátku.
Přesuny pracují s plochým pořadím (záhlaví skupiny, pak její symboly), takže
symbol patří vždy do skupiny nad sebou.
"""

# Řazení, kterými postupně prochází klik na záhlaví sloupce: Symbol = abeceda
# A→Z / Z→A, vlaječka = barvy v pořadí palety / obráceně; po posledním následuje
# vlastní pořadí (""), z jiného řazení se začíná prvním
SORT_CYCLES: dict[str, tuple[str, str]] = {"symbol": ("asc", "desc"), "flag": ("flag", "flag_desc")}
# Všechna platná řazení ("" = vlastní pořadí)
WATCHLIST_SORTS: tuple[str, ...] = ("",) + tuple(sort for cycle in SORT_CYCLES.values() for sort in cycle)

# Barvy vlaječek symbolů v pořadí palety (a řazení) jako v TradingView;
# stejné pořadí drží static/watchlist_flags.js, odstíny static/style.css
FLAG_COLORS: tuple[str, ...] = ("red", "blue", "green", "orange", "purple", "cyan", "pink")

# Skupina seznamu: id sekce (None = bez sekce) a klíče "zdroj:SYMBOL" v pořadí
Group = tuple[int | None, list[str]]


def sort_watchlist(items: list[dict], sort: str) -> list[dict]:
    """Symboly seznamu v požadovaném pořadí.

    Abeceda nerozlišuje velikost písmen; shodný symbol z více zdrojů se řadí
    podle zdroje (u Z→A také vzestupně). Vlaječky se řadí podle pořadí barev
    v paletě, symboly bez vlaječky na konec (obráceně na začátek); stejná barva
    drží vlastní pořadí. Neznámé řazení = vlastní pořadí.

    Parametry:
        items: symboly [{"source", "symbol", "flag"}] ve vlastním pořadí.
        sort: klíč WATCHLIST_SORTS.
    """
    if sort_column(sort) == "flag":
        # Stabilní řazení – reverse=True zachová vlastní pořadí uvnitř barvy
        return sorted(items, key=lambda item: _flag_rank(item.get("flag")), reverse=sort == "flag_desc")
    if sort not in ("asc", "desc"):
        return list(items)
    # Nejdřív podle zdroje, pak stabilně podle symbolu – zdroj zůstane vzestupně i u Z→A
    by_source = sorted(items, key=lambda item: item["source"])
    return sorted(by_source, key=lambda item: item["symbol"].casefold(), reverse=sort == "desc")


def _flag_rank(flag) -> int:
    # Pořadí barvy v paletě; bez vlaječky (i neznámá barva) za všemi barvami
    return FLAG_COLORS.index(flag) if flag in FLAG_COLORS else len(FLAG_COLORS)


def sort_column(sort: str) -> str | None:
    """Sloupec záhlaví ("symbol" / "flag"), podle kterého řazení třídí; None = vlastní pořadí."""
    return next((column for column, cycle in SORT_CYCLES.items() if sort in cycle), None)


def next_sort(current: str, column: str) -> str:
    """Řazení po kliknutí na záhlaví sloupce (Symbol / vlaječka) jako v TradingView.

    Parametry:
        current: aktuální klíč WATCHLIST_SORTS.
        column: "symbol" nebo "flag" (klíč SORT_CYCLES).
    """
    cycle = SORT_CYCLES[column] + ("",)
    return cycle[cycle.index(current) + 1] if current in cycle[:-1] else cycle[0]


def click_flag(current: str | None, clicked: str | None, last: str | None) -> str | None:
    """Vlaječka symbolu po kliknutí (jako v TradingView); None = bez vlaječky.

    Klik na vlaječku řádku přidá naposledy použitou barvu, nebo vlaječku
    odebere; klik na barvu palety ji nastaví, klik na barvu, kterou symbol
    už má, vlaječku odebere.

    Parametry:
        current: současná barva symbolu (None = bez vlaječky).
        clicked: barva z palety; None (i neznámá barva) = klik na vlaječku řádku.
        last: naposledy použitá barva (neznámá = první barva palety).
    """
    color = clicked if clicked in FLAG_COLORS else current or last
    if color not in FLAG_COLORS:
        color = FLAG_COLORS[0]
    return None if color == current else color


def item_key(item: dict) -> str:
    """Klíč symbolu seznamu "zdroj:SYMBOL" (stejný jako klíč grafu)."""
    return f"{item['source']}:{item['symbol']}"


def build_groups(items: list[dict], sections: list[dict], free_position: int = 0) -> list[Group]:
    """Skupiny seznamu v pořadí zobrazení (sekce i skupina symbolů bez sekce).

    Symbol s neznámou sekcí (smazanou) spadne mezi symboly bez sekce.

    Parametry:
        items: symboly [{"source", "symbol", "section_id"}] ve vlastním pořadí.
        sections: sekce [{"id", "position"}] seznamu.
        free_position: pořadí skupiny bez sekce mezi sekcemi (při shodě je první).
    """
    order = sorted([(free_position, 0, None)] + [(section["position"], 1, section["id"]) for section in sections],
                   key=lambda entry: entry[:2])
    groups: list[Group] = [(section_id, []) for _position, _kind, section_id in order]
    keys_by_section = dict(groups)
    for item in items:
        keys_by_section.get(item.get("section_id"), keys_by_section[None]).append(item_key(item))
    return _normalize(groups)


def _normalize(groups: list[Group]) -> list[Group]:
    # Prázdná skupina bez sekce je skrytá – držíme ji na začátku, aby šel symbol
    # puštěním nad první sekci vrátit mezi symboly bez sekce
    free = next(group for group in groups if group[0] is None)
    if free[1] or groups[0] is free:
        return groups
    return [free] + [group for group in groups if group is not free]


def _flatten(groups: list[Group]) -> list[tuple[str, object]]:
    # Ploché pořadí: záhlaví ("section", id) každé skupiny (i bez sekce, id None),
    # za ním její symboly ("symbol", klíč)
    flat: list[tuple[str, object]] = []
    for section_id, keys in groups:
        flat.append(("section", section_id))
        flat.extend(("symbol", key) for key in keys)
    return flat


def _regroup(flat: list[tuple[str, object]]) -> list[Group]:
    # Opak _flatten: symbol patří do poslední skupiny nad sebou (první prvek je
    # vždy záhlaví – move_symbol nevkládá před něj)
    groups: list[Group] = []
    for kind, value in flat:
        if kind == "section":
            groups.append((value, []))
        else:
            groups[-1][1].append(value)
    return _normalize(groups)


def move_symbol(groups: list[Group], key: str, target: tuple[str, object] | None, after: bool) -> list[Group]:
    """Přesune symbol tažením myši; vrátí nové skupiny (vstup nemění).

    Puštění na symbol: před / za něj (do jeho skupiny). Puštění na záhlaví:
    za záhlaví = na začátek skupiny, před záhlaví = na konec předchozí skupiny
    (nad první sekcí = mezi symboly bez sekce, nebo na začátek první sekce,
    když symboly bez sekce stojí jinde). Bez cíle = na konec seznamu.

    Parametry:
        groups: skupiny seznamu (build_groups).
        key: klíč přesouvaného symbolu.
        target: ("symbol", klíč) / ("section", id) nebo None = konec seznamu.
        after: True = za cíl (dolní polovina řádku), False = před cíl.
    """
    flat = [entry for entry in _flatten(groups) if entry != ("symbol", key)]
    if target is None or target not in flat:
        index = len(flat)
    else:
        index = max(flat.index(target) + (1 if after else 0), 1)
    flat.insert(index, ("symbol", key))
    return _regroup(flat)


def move_section(groups: list[Group], section_id: int | None, target: tuple[str, object] | None, after: bool) -> list[Group]:
    """Přesune skupinu (sekci, nebo symboly bez sekce) před / za jinou skupinu.

    Puštění na symbol = za jeho skupinu; do horní poloviny prvního symbolu
    skupiny = před ni (tak jde sekce i nad symboly bez sekce na začátek
    seznamu). Puštění na vlastní skupinu nic nezmění.

    Parametry:
        groups: skupiny seznamu (build_groups).
        section_id: přesouvaná sekce (None = skupina symbolů bez sekce).
        target: ("section", id) / ("symbol", klíč) nebo None = konec seznamu.
        after: True = za cílovou skupinu, False = před ni.
    """
    moved = next((group for group in groups if group[0] == section_id), None)
    target_group = None
    if target and target[0] == "symbol":
        target_group = next((group for group in groups if target[1] in group[1]), None)
        after = after or (target_group is not None and target_group[1][0] != target[1])
    elif target:
        target_group = next((group for group in groups if group[0] == target[1]), None)
    if moved is None or target_group is moved:
        return list(groups)
    # Skupiny se jen přeskládají – seznamy symbolů zůstávají sdílené (nemění se)
    rest = [group for group in groups if group is not moved]
    index = rest.index(target_group) + after if target_group is not None else len(rest)
    rest.insert(index, moved)
    return _normalize(rest)


def remove_section(groups: list[Group], section_id: int) -> list[Group]:
    """Odebere sekci; její symboly připadnou předchozí skupině (jako v TradingView),
    u první sekce na začátek skupiny symbolů bez sekce.

    Parametry:
        groups: skupiny seznamu (build_groups).
        section_id: odebíraná sekce.
    """
    index = next((position for position, group in enumerate(groups) if group[0] == section_id), None)
    if index is None:
        return list(groups)
    rest = [(group_id, list(keys)) for group_id, keys in groups if group_id != section_id]
    receiver_id = groups[index - 1][0] if index > 0 else None
    receiver = next(group for group in rest if group[0] == receiver_id)
    if index > 0:
        receiver[1].extend(groups[index][1])
    else:
        receiver[1][:0] = groups[index][1]
    return _normalize(rest)


def parse_entry(text) -> tuple[str, object] | None:
    """Položka seznamu z atributu data-wl-entry (static/watchlist_dnd.js).

    "symbol:zdroj:SYMBOL" -> ("symbol", "zdroj:SYMBOL"), "section:5" -> ("section", 5),
    "section:none" (záhlaví Bez sekce) -> ("section", None); cokoli jiného (i None) -> None.
    """
    kind, _, value = str(text or "").partition(":")
    if kind == "symbol" and value:
        return "symbol", value
    if kind == "section" and value.isdigit():
        return "section", int(value)
    if kind == "section" and value == "none":
        return "section", None
    return None
