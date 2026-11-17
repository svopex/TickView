/* TickView – výběr barvy ve stylu TradingView.
 *
 * Vyskakovací panel: řada šedých odstínů, řada hlavních barev, šest řad
 * odstínů, vlastní barvy (tlačítko + otevře systémový výběr) a posuvník
 * průhlednosti s polem v procentech. Panel otevírá Vue komponenta
 * ColorButton (color_button.js); výsledná barva je #rrggbb, nebo
 * rgba(r, g, b, a), když průhlednost není 100 %. Tlačítko stylu výstupu
 * indikátoru (plot_style_button.js) přidá pod průhlednost volbu tloušťky
 * (Thickness) a stylu čáry (Line style) jako v TradingView.
 */
window.TickViewColorPicker = (function () {
  'use strict';

  // Šedé odstíny (první řada) a hlavní barvy (druhá řada) jako v TradingView
  const GRAYS = ['#ffffff', '#d1d4dc', '#b2b5be', '#9598a1', '#787b86', '#5d606b', '#434651', '#2a2e39', '#131722', '#000000'];
  const MAIN = ['#f23645', '#ff9800', '#ffeb3b', '#4caf50', '#089981', '#00bcd4', '#2962ff', '#673ab7', '#9c27b0', '#e91e63'];
  // Odstíny hlavních barev od nejsvětlejšího po nejtmavší (sloupce = MAIN)
  const SHADES = [
    ['#fccbcd', '#ffe0b2', '#fff9c4', '#c8e6c9', '#ace5dc', '#b2ebf2', '#bbd9fb', '#d1c4e9', '#e1bee7', '#f8bbd0'],
    ['#faa1a4', '#ffcc80', '#fff59d', '#a5d6a7', '#70ccbd', '#80deea', '#90bff9', '#b39ddb', '#ce93d8', '#f48fb1'],
    ['#f77c80', '#ffb74d', '#fff176', '#81c784', '#42bda8', '#4dd0e1', '#5b9cf6', '#9575cd', '#ba68c8', '#f06292'],
    ['#f7525f', '#ffa726', '#ffee58', '#66bb6a', '#22ab94', '#26c6da', '#3179f5', '#7e57c2', '#ab47bc', '#ec407a'],
    ['#b22833', '#f57c00', '#f9a825', '#388e3c', '#056656', '#0097a7', '#1848cc', '#512da8', '#7b1fa2', '#c2185b'],
    ['#801922', '#e65100', '#f57f17', '#1b5e20', '#00332a', '#006064', '#0c3299', '#311b92', '#4a148c', '#880e4f'],
  ];
  // Klíč localStorage s vlastními barvami a jejich nejvyšší počet (jedna řada)
  const CUSTOM_KEY = 'tickview.customColors';
  const MAX_CUSTOM = 10;

  // Otevřený panel: prvek, kotva, callbacky a aktuální barva {r, g, b, a}
  let current = null;

  // Převede zápis barvy (#rrggbb nebo rgb()/rgba() – jediné tvary, které
  // aplikace ukládá) na {r, g, b, a}; jinak null
  function parseColor(value) {
    const text = String(value || '').trim();
    let match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(text);
    if (match) return { r: parseInt(match[1], 16), g: parseInt(match[2], 16), b: parseInt(match[3], 16), a: 1 };
    // rgb(r, g, b) / rgba(r, g, b, a) – průhlednost je volitelná
    match = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(\d*\.?\d+)\s*)?\)$/i.exec(text);
    if (match) {
      return {
        r: Math.min(255, +match[1]),
        g: Math.min(255, +match[2]),
        b: Math.min(255, +match[3]),
        a: match[4] !== undefined ? Math.min(1, +match[4]) : 1,
      };
    }
    return null;
  }

  // #rrggbb z barevných složek
  function toHex(color) {
    return '#' + [color.r, color.g, color.b].map((part) => part.toString(16).padStart(2, '0')).join('');
  }

  // Výsledný zápis barvy: plně krycí #rrggbb, jinak rgba() se 2 desetinnými místy
  function formatColor(color) {
    const alpha = Math.round(color.a * 100) / 100;
    if (alpha >= 1) return toHex(color);
    return 'rgba(' + color.r + ', ' + color.g + ', ' + color.b + ', ' + alpha + ')';
  }

  // Vlastní barvy z localStorage (může být nedostupné – soukromé okno, náhled)
  function loadCustom() {
    try {
      const list = JSON.parse(window.localStorage.getItem(CUSTOM_KEY) || '[]');
      return Array.isArray(list) ? list.filter((hex) => /^#[0-9a-f]{6}$/i.test(hex)).slice(0, MAX_CUSTOM) : [];
    } catch (error) {
      return [];
    }
  }

  // Uloží vlastní barvy; nedostupné úložiště se tiše ignoruje (barva platí aspoň teď)
  function saveCustom(list) {
    try {
      window.localStorage.setItem(CUSTOM_KEY, JSON.stringify(list.slice(0, MAX_CUSTOM)));
    } catch (error) {
      // Úložiště nedostupné – vlastní barva nepřetrvá, výběr ale funguje
    }
  }

  // Pomocná tvorba prvku s třídou (a případným textem)
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Oznámí novou barvu komponentě (ta ji pošle do Pythonu) a promítne ji do
  // panelu; except = prvek, ze kterého změna přišla (jeho hodnotu nepřepisujeme)
  function emit(except) {
    if (!current) return;
    current.onChange(formatColor(current.color));
    sync(except);
  }

  // Promítne aktuální barvu do postaveného panelu: zvýraznění vzorku, přechod
  // posuvníku, hodnoty posuvníku a pole v % (bez přestavby prvků)
  function sync(except) {
    const popup = current.popup;
    const hex = toHex(current.color);
    const percent = String(Math.round(current.color.a * 100));
    popup.style.setProperty('--tv-cp-rgb', current.color.r + ', ' + current.color.g + ', ' + current.color.b);
    popup.querySelectorAll('.tv-cp-swatch').forEach((node) => node.classList.toggle('selected', node.title === hex));
    if (except !== current.alphaRange) current.alphaRange.value = percent;
    if (except !== current.alphaInput) current.alphaInput.value = percent + '%';
  }

  // Vybere barvu ze vzorníku: odstín se změní, průhlednost zůstane
  function pickHex(hex) {
    const parsed = parseColor(hex);
    if (!parsed || !current) return;
    current.color = { r: parsed.r, g: parsed.g, b: parsed.b, a: current.color.a };
    emit();
  }

  // Čtvereček vzorníku (title = barva; podle něj sync() zvýrazní vybraný)
  function swatch(hex) {
    const node = el('button', 'tv-cp-swatch');
    node.type = 'button';
    node.title = hex;
    node.style.setProperty('--tv-cp-swatch', hex);
    node.addEventListener('click', (event) => {
      event.stopPropagation();
      pickHex(hex);
    });
    return node;
  }

  // Řada vzorníku (10 barev v mřížce)
  function swatchRow(colors, extraClass) {
    const row = el('div', 'tv-cp-row' + (extraClass ? ' ' + extraClass : ''));
    for (const hex of colors) row.appendChild(swatch(hex));
    return row;
  }

  // Řada vlastních barev a tlačítko „+“ (systémový výběr barvy); přestaví se
  // jen po přidání vlastní barvy
  function renderCustomRow(row) {
    row.innerHTML = '';
    for (const hex of loadCustom()) row.appendChild(swatch(hex));
    // Ikona v samostatném spanu – prvek button má font z resetu stylů, ne font ikon
    const add = el('button', 'tv-cp-add');
    add.type = 'button';
    add.appendChild(el('span', 'material-icons', 'add'));
    add.title = 'Vlastní barva';
    const native = el('input', 'tv-cp-native');
    native.type = 'color';
    native.value = toHex(current.color);
    add.addEventListener('click', (event) => {
      event.stopPropagation();
      native.click();
    });
    // Systémový výběr: barva se hned použije a po potvrzení uloží mezi vlastní
    native.addEventListener('input', () => pickHex(native.value));
    native.addEventListener('change', () => {
      const hex = native.value.toLowerCase();
      saveCustom([hex].concat(loadCustom().filter((item) => item !== hex)));
      renderCustomRow(row);
      pickHex(hex);
    });
    row.appendChild(add);
    row.appendChild(native);
  }

  // Postaví obsah panelu (jednou při otevření; změny promítá sync())
  function render() {
    const popup = current.popup;
    popup.appendChild(swatchRow(GRAYS));
    popup.appendChild(swatchRow(MAIN, 'tv-cp-row-main'));
    for (const row of SHADES) popup.appendChild(swatchRow(row));
    popup.appendChild(el('div', 'tv-cp-sep'));
    const customRow = el('div', 'tv-cp-row tv-cp-custom');
    renderCustomRow(customRow);
    popup.appendChild(customRow);

    // Průhlednost: posuvník (šachovnice + přechod do aktuální barvy) a pole v %
    popup.appendChild(el('div', 'tv-cp-label', 'Opacity'));
    const alphaRow = el('div', 'tv-cp-alpha');
    const range = el('input', 'tv-cp-alpha-range');
    range.type = 'range';
    range.min = '0';
    range.max = '100';
    range.addEventListener('input', () => {
      current.color.a = Number(range.value) / 100;
      emit(range);
    });
    const input = el('input', 'tv-cp-alpha-input');
    input.type = 'text';
    // Pole v %: platná hodnota 0–100 se použije hned při psaní
    input.addEventListener('input', () => {
      const value = parseInt(input.value.replace('%', ''), 10);
      if (isNaN(value)) return;
      current.color.a = Math.max(0, Math.min(100, value)) / 100;
      emit(input);
    });
    // Klik do pole označí celý obsah – nová hodnota přepíše původní
    input.addEventListener('focus', () => input.select());
    input.addEventListener('mouseup', (event) => event.preventDefault());
    input.addEventListener('blur', () => {
      if (current) sync();
    });
    alphaRow.appendChild(range);
    alphaRow.appendChild(input);
    popup.appendChild(alphaRow);
    current.alphaRange = range;
    current.alphaInput = input;
    if (current.line) renderLineOptions(popup);
    sync();
  }

  // Skupina přepínacích tlačítek (Thickness / Line style) s náhledem čáry;
  // values = hodnoty tlačítek, selected = zvolená, preview(node, hodnota) =
  // vzhled náhledu, onPick(hodnota) = volba uživatele
  function segmented(values, selected, preview, onPick) {
    const group = el('div', 'tv-cp-segmented');
    const buttons = values.map((value) => {
      const button = el('button', 'tv-cp-segment' + (value === selected ? ' selected' : ''));
      button.type = 'button';
      const line = el('span', 'tv-cp-line-preview');
      preview(line, value);
      button.appendChild(line);
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        buttons.forEach((node) => node.classList.toggle('selected', node === button));
        onPick(value);
      });
      group.appendChild(button);
      return button;
    });
    return group;
  }

  // Tloušťka a styl čáry výstupu indikátoru pod průhledností; změna jde přes
  // current.line.onChange({width, lineStyle}) do komponenty tlačítka
  function renderLineOptions(popup) {
    const line = current.line;
    const notify = () => line.onChange({ width: line.width, lineStyle: line.lineStyle });
    popup.appendChild(el('div', 'tv-cp-sep'));
    popup.appendChild(el('div', 'tv-cp-label', 'Thickness'));
    popup.appendChild(
      segmented(
        line.widths,
        line.width,
        (node, width) => node.style.setProperty('--tv-cp-line-width', width + 'px'),
        (width) => {
          line.width = width;
          notify();
        }
      )
    );
    popup.appendChild(el('div', 'tv-cp-label', 'Line style'));
    popup.appendChild(
      segmented(
        line.lineStyles,
        line.lineStyle,
        (node, style) => node.classList.add('tv-line-' + style),
        (style) => {
          line.lineStyle = style;
          notify();
        }
      )
    );
  }

  // Umístí panel pod kotvu (případně nad ni), aby nepřetekl okraj okna
  function place(popup, anchor) {
    const rect = anchor.getBoundingClientRect();
    const size = popup.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 4;
    if (left + size.width > window.innerWidth - 8) left = window.innerWidth - size.width - 8;
    if (top + size.height > window.innerHeight - 8) top = rect.top - size.height - 4;
    popup.style.left = Math.max(8, left) + 'px';
    popup.style.top = Math.max(8, top) + 'px';
  }

  // Klik mimo panel a kotvu panel zavře
  function onOutsidePointer(event) {
    if (!current) return;
    if (current.popup.contains(event.target) || current.anchor.contains(event.target)) return;
    close();
  }

  // Esc panel zavře (a nezavře přitom dialog Settings pod ním)
  function onKeyDown(event) {
    if (current && event.key === 'Escape') {
      event.stopPropagation();
      close();
    }
  }

  // Otevře panel u kotvy.
  // Parametry: anchor – tlačítko barvy, value – aktuální barva,
  // onChange(barva) – nová barva, onClose() – panel se zavřel,
  // line – volitelně volby čáry {width, widths, lineStyle, lineStyles, onChange({width, lineStyle})}
  // (panel pak nabídne i Thickness a Line style)
  function open(anchor, value, onChange, onClose, line) {
    close();
    const popup = el('div', 'tv-color-picker');
    const parsed = parseColor(value) || { r: 41, g: 98, b: 255, a: 1 };
    current = {
      popup: popup,
      anchor: anchor,
      onChange: onChange,
      onClose: onClose,
      color: parsed,
      line: line ? Object.assign({}, line) : null,
    };
    // V dialogu vkládáme panel do jeho vnitřku – Quasar jinak vrací fokus do
    // dialogu (pole procent by nešlo upravit); mimo dialog do body
    const host = anchor.closest('.q-dialog__inner') || document.body;
    host.appendChild(popup);
    render();
    place(popup, anchor);
    document.addEventListener('pointerdown', onOutsidePointer, true);
    document.addEventListener('keydown', onKeyDown, true);
  }

  // Zavře otevřený panel (pokud existuje)
  function close() {
    if (!current) return;
    const closing = current;
    current = null;
    closing.popup.remove();
    document.removeEventListener('pointerdown', onOutsidePointer, true);
    document.removeEventListener('keydown', onKeyDown, true);
    if (closing.onClose) closing.onClose();
  }

  // Je panel otevřený pro danou kotvu?
  function isOpenFor(anchor) {
    return !!current && current.anchor === anchor;
  }

  return { open: open, close: close, isOpenFor: isOpenFor, parseColor: parseColor, formatColor: formatColor };
})();
