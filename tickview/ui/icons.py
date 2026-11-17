"""Tenké SVG piktogramy lišty kreslení (vlastní kresba ve stylu TradingView).

Ikony jsou 28×28 s tahem v currentColor, takže přebírají barvu textu
(motiv, zvýraznění aktivního nástroje). Stejné SVG používá Python (lišta)
i JS graf (flyout nabídky, plovoucí panel oblíbených) – do JS se posílají
jednou přes LightweightChart.set_icons.
"""

# Vnitřek SVG jednotlivých ikon (cesty v souřadnicích 0–28); kroužky
# znázorňují kotevní body kresby jako v TradingView
_ICON_PATHS: dict[str, str] = {
    # Kurzor: kříž (crosshair)
    "cursor": '<path d="M14 5v7M14 16v7M5 14h7M16 14h7"/>',
    # Čáry
    "trend": '<circle cx="7" cy="21" r="1.8"/><circle cx="21" cy="7" r="1.8"/><path d="M8.3 19.7 19.7 8.3"/>',
    "ray": '<circle cx="7" cy="21" r="1.8"/><path d="M8.3 19.7 23.5 4.5"/>',
    "extended": '<circle cx="11" cy="17" r="1.8"/><circle cx="17" cy="11" r="1.8"/>'
                '<path d="M4.5 23.5 9.7 18.3M12.3 15.7l3.4-3.4M18.3 9.7l5.2-5.2"/>',
    "arrow": '<path d="M6 22 21 7M14 7h7v7"/>',
    "hline": '<circle cx="14" cy="14" r="1.8"/><path d="M4 14h8.2M15.8 14H24"/>',
    "hray": '<circle cx="7" cy="14" r="1.8"/><path d="M8.8 14H24"/>',
    "vline": '<circle cx="14" cy="14" r="1.8"/><path d="M14 4v8.2M14 15.8V24"/>',
    "cross": '<circle cx="14" cy="14" r="1.8"/><path d="M14 4v8.2M14 15.8V24M4 14h8.2M15.8 14H24"/>',
    # Kanál: dvě rovnoběžky s kotevními body
    "channel": '<circle cx="5.5" cy="16.5" r="1.8"/><circle cx="16.5" cy="5.5" r="1.8"/><circle cx="22.5" cy="11.5" r="1.8"/>'
               '<path d="M6.8 15.2l8.4-8.4M11 23l10.2-10.2"/>',
    # Pitchfork: rukojeť a tři hroty
    "pitchfork": '<circle cx="14" cy="23" r="1.8"/><path d="M14 21.2V6M7 6v5a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4V6"/>',
    # Fibonacci: úrovně retracementu s kotevními body nahoře a dole
    "fib": '<circle cx="6" cy="6" r="1.8"/><circle cx="6" cy="22" r="1.8"/>'
           '<path d="M8 6h16M4 10.5h20M4 14.5h20M8 22h16"/>',
    # Fibonacci extension: úrovně nad lomenou čarou ze tří bodů
    "fibext": '<path d="M11 5h13M11 9h13M11 13h13"/><circle cx="5" cy="22" r="1.8"/><circle cx="23" cy="20" r="1.8"/>'
              '<path d="M6.3 20.7 10 17l4 4.5 7.3-1.3"/>',
    # Tvary
    "rect": '<path d="M7.8 8H22v12H7.8M6 9.8V20"/><circle cx="6" cy="8" r="1.8"/><circle cx="22" cy="20" r="1.8"/>',
    "brush": '<path d="M23 5 13.5 14.5"/><path d="M13.5 14.5c-2-1.2-4.4-.4-5.3 1.6-.9 2-.6 4.2-3.2 5.4 3.3 1.3 7.2.8 8.5-2 .8-1.7.8-3.5 0-5z"/>',
    # Text a sticker
    "text": '<path d="M8 7h12M14 7v15M11 22h6"/>',
    "sticker": '<circle cx="14" cy="14" r="9"/><path d="M10.5 16.5c2 2.6 5 2.6 7 0"/>'
               '<circle cx="11" cy="11.5" r=".6" fill="currentColor"/><circle cx="17" cy="11.5" r=".6" fill="currentColor"/>',
    # Long / Short position jako v TradingView: horní a dolní čára s kotevním
    # bodem vlevo, mezi nimi čára vstupu přes celou šířku; písmeno L leží
    # v (větší) zóně cíle nad vstupem, písmeno S v zóně cíle pod vstupem
    "long": '<circle cx="5.5" cy="5" r="1.8"/><path d="M7.3 5H24"/>'
            '<path d="M4 17h20"/>'
            '<circle cx="5.5" cy="23" r="1.8"/><path d="M7.3 23H24"/>'
            '<path d="M12.6 8.3v5.4h3.4"/>',
    "short": '<circle cx="5.5" cy="5" r="1.8"/><path d="M7.3 5H24"/>'
             '<path d="M4 11h20"/>'
             '<circle cx="5.5" cy="23" r="1.8"/><path d="M7.3 23H24"/>'
             '<path d="M15.8 15.1c-.3-.7-1-1.1-1.8-1.1-1 0-1.8.6-1.8 1.4 0 1.9 3.7 1.1 3.7 3.2 0 .9-.8 1.5-1.9 1.5-.9 0-1.6-.4-1.9-1.2"/>',
    # Měřič: pravítko s dílky
    "measure": '<path d="M4 19 19 4l5 5L9 24z"/><path d="M7.5 15.5l2 2M10.5 12.5l3 3M13.5 9.5l2 2M16.5 6.5l3 3"/>',
    # Magnet (a vypnutý magnet s přeškrtnutím)
    "magnet": '<path d="M7 5h5v10a2 2 0 0 0 4 0V5h5v10a7 7 0 0 1-14 0z"/><path d="M7 9h5M16 9h5"/>',
    "magnet_off": '<path d="M7 5h5v10a2 2 0 0 0 4 0V5h5v10a7 7 0 0 1-14 0z"/><path d="M7 9h5M16 9h5M4 24 24 4"/>',
    # Režim kreslení (tužka)
    "pencil": '<path d="M6 22l1.2-5L18 6.2l3.8 3.8L11 20.8z"/><path d="M16 8.2l3.8 3.8"/>',
    # Zámek zamčený / odemčený
    "lock": '<rect x="8" y="13" width="12" height="10" rx="1.5"/><path d="M10.5 13v-3a3.5 3.5 0 0 1 7 0v3M14 17v2.5"/>',
    "lock_open": '<rect x="8" y="13" width="12" height="10" rx="1.5"/><path d="M10.5 13v-3a3.5 3.5 0 0 1 6.7-1.4M14 17v2.5"/>',
    # Oko (viditelnost kreseb)
    "eye": '<path d="M3.5 14S7.5 7.5 14 7.5 24.5 14 24.5 14 20.5 20.5 14 20.5 3.5 14 3.5 14z"/><circle cx="14" cy="14" r="3"/>',
    "eye_off": '<path d="M3.5 14S7.5 7.5 14 7.5 24.5 14 24.5 14 20.5 20.5 14 20.5 3.5 14 3.5 14z"/><circle cx="14" cy="14" r="3"/>'
               '<path d="M5 23 23 5"/>',
    # Koš
    "trash": '<path d="M6.5 8h15M11 8V5.8h6V8M8.8 8l1 15h8.4l1-15M12.5 11.5v8M15.5 11.5v8"/>',
    # Typy grafu (nabídka v horní liště)
    "chart_bars": '<path d="M9 5v18M6 8h3M9 19h3M19 7v15M16 12h3M19 17h3"/>',
    "chart_candles": '<path d="M9 4v4M9 19v5M19 6v3M19 16v5"/><rect x="7" y="8" width="4" height="11" rx=".5"/>'
                     '<rect x="17" y="9" width="4" height="7" rx=".5" fill="currentColor"/>',
    "chart_hollow": '<path d="M9 4v4M9 19v5M19 6v3M19 16v5"/><rect x="7" y="8" width="4" height="11" rx=".5"/>'
                    '<rect x="17" y="9" width="4" height="7" rx=".5"/>',
    "chart_heikin": '<path d="M9 5v4M9 18v5M19 7v3M19 15v6"/><rect x="7" y="9" width="4" height="9" rx=".5"/>'
                    '<rect x="17" y="10" width="4" height="5" rx=".5" fill="currentColor"/><path d="M13 12h2"/>',
    "chart_highlow": '<rect x="7" y="6" width="4" height="16" rx=".5"/><rect x="17" y="10" width="4" height="9" rx=".5"/>',
    "chart_line": '<path d="M4 20l6-8 5 4 9-10"/>',
    "chart_line_markers": '<path d="M5.4 18.6 8.8 14M11.6 12.9l2.3 1.9M16.3 15l6.3-7.4"/>'
                          '<circle cx="4.5" cy="20" r="1.5"/><circle cx="10" cy="12.2" r="1.5"/>'
                          '<circle cx="15" cy="16" r="1.5"/><circle cx="23.5" cy="6.5" r="1.5"/>',
    "chart_step": '<path d="M4 22v-6h6v-6h7V5h7"/>',
    "chart_area": '<path d="M4 20l6-8 5 4 9-10v18H4z" fill="currentColor" fill-opacity=".25" stroke="none"/>'
                  '<path d="M4 20l6-8 5 4 9-10"/>',
    "chart_baseline": '<path d="M3 14h22" stroke-dasharray="2 2"/><path d="M4 19l5-8 5 6 5-9 5 4"/>',
    "chart_columns": '<rect x="5" y="15" width="4" height="8" rx=".5"/><rect x="12" y="8" width="4" height="15" rx=".5"/>'
                     '<rect x="19" y="12" width="4" height="11" rx=".5"/>',
    # Druhy vykreslení výstupu indikátoru (Plot type v záložce Style)
    "plot_line": '<path d="M4 17c3-5 5-5 7-2s4 3 6-1 4-4 7-1"/>',
    "plot_linebr": '<path d="M4 18l4-5M11 11l2-2M16 10l3 3M22 11l2-2"/><path d="M14 4v20" stroke-dasharray="1.5 2"/>',
    "plot_step": '<path d="M5 22v-8h5V6h6v10h5v-6h3"/>',
    "plot_stepbr": '<path d="M5 22v-8h5V6h3M16 6v10h5v-6h3"/><path d="M14.5 4v20" stroke-dasharray="1.5 2"/>',
    "plot_stepdiamond": '<path d="M7 23V7h7v13h4"/><path d="M18 17.5l2.5 2.5-2.5 2.5-2.5-2.5z"/>',
    "plot_histogram": '<path d="M5 23v-6M8 23v-9M11 23v-4M14 23v-11M17 23v-7M20 23v-10M23 23v-5"/>',
    "plot_area": '<path d="M4 22V14l5-4 5 3 5-6 5 3v12z"/>',
    "plot_areabr": '<path d="M4 22v-8l5-4 3 2v10zM17 22V9l2-2 5 3v12z"/><path d="M14.5 4v20" stroke-dasharray="1.5 2"/>',
    "plot_columns": '<rect x="5" y="15" width="4" height="8"/><rect x="12" y="8" width="4" height="15"/>'
                    '<rect x="19" y="12" width="4" height="11"/>',
    "plot_circles": '<circle cx="7" cy="18" r="2.2"/><circle cx="14" cy="9" r="2.2"/><circle cx="21" cy="15" r="2.2"/>',
    # Hvězda (panel oblíbených zobrazen / skryt)
    "star": '<path fill="currentColor" d="M14 4.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L14 19.4l-5.9 3.1 1.2-6.5-4.8-4.6 6.6-.9z"/>',
    "star_border": '<path d="M14 4.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L14 19.4l-5.9 3.1 1.2-6.5-4.8-4.6 6.6-.9z"/>',
}


def svg(name: str) -> str:
    """Celé SVG ikony podle názvu (neznámý název = prázdné SVG).

    Parametry:
        name: klíč ikony, např. "trend" nebo "magnet".
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" fill="none" stroke="currentColor" '
        'stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">'
        f"{_ICON_PATHS.get(name, '')}</svg>"
    )


# Všechny ikony jako celé SVG (pro JS graf)
ICONS: dict[str, str] = {name: svg(name) for name in _ICON_PATHS}
