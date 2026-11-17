// Tlačítko stylu výstupu indikátoru (záložka Style jako v TradingView):
// vzorek barvy a náhled čáry; klik otevře výběr barvy (window.TickViewColorPicker)
// s průhledností, tloušťkou (Thickness) a stylem čáry (Line style). Hodnota
// {color, width, lineStyle} se posílá do Pythonu událostí update:value.
export default {
  template: `<button type="button" class="tv-color-btn tv-plot-style-btn" :class="{ 'tv-color-btn-open': open }" :title="title" @click.stop="toggle"><span class="tv-color-btn-fill tv-plot-style-fill" ref="fill"></span><span class="tv-plot-style-line" ref="line"></span></button>`,
  props: {
    value: Object,
    title: String,
    widths: Array,
    lineStyles: Array,
  },
  data() {
    return { open: false, current: Object.assign({}, this.value) };
  },
  watch: {
    // Nová hodnota z Pythonu (vyplnění dialogu, šablona) přepíše místní kopii
    value(next) {
      this.current = Object.assign({}, next);
      this.paint();
    },
  },
  mounted() {
    this.paint();
  },
  beforeUnmount() {
    // Zrušený prvek nesmí nechat otevřený panel
    if (window.TickViewColorPicker.isOpenFor(this.$el)) window.TickViewColorPicker.close();
  },
  methods: {
    // Vzorek barvy a náhled čáry (barva, tloušťka a styl) přes CSS proměnné a třídu
    paint() {
      const { fill, line } = this.$refs;
      if (!fill || !line) return;
      const value = this.current;
      fill.style.setProperty("--tv-swatch", value.color || "transparent");
      line.style.setProperty("--tv-plot-color", value.color || "currentColor");
      line.style.setProperty("--tv-plot-width", value.width + "px");
      line.className = "tv-plot-style-line tv-line-" + value.lineStyle;
    },
    // Místní změna (barva nebo čára) se hned promítne do tlačítka a pošle do Pythonu
    change(patch) {
      this.current = Object.assign({}, this.current, patch);
      this.paint();
      this.$emit("update:value", Object.assign({}, this.current));
    },
    // Klik otevře panel; druhý klik na stejné tlačítko ho zavře
    toggle() {
      const picker = window.TickViewColorPicker;
      if (picker.isOpenFor(this.$el)) {
        picker.close();
        return;
      }
      this.open = true;
      const value = this.current;
      picker.open(
        this.$el,
        value.color,
        (color) => this.change({ color: color }),
        () => {
          this.open = false;
        },
        {
          width: value.width,
          widths: this.widths,
          lineStyle: value.lineStyle,
          lineStyles: this.lineStyles,
          onChange: (line) => this.change(line),
        }
      );
    },
  },
};
