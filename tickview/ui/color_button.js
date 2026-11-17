// Tlačítko barvy (vzorek) – klik otevře výběr barvy ve stylu TradingView
// (window.TickViewColorPicker ze static/color_picker.js); zvolená barva se
// posílá do Pythonu událostí update:value.
export default {
  template: `<button type="button" class="tv-color-btn" :class="{ 'tv-color-btn-open': open }" :title="title" @click.stop="toggle"><span class="tv-color-btn-fill" ref="fill"></span></button>`,
  props: {
    value: String,
    title: String,
  },
  data() {
    return { open: false };
  },
  mounted() {
    this.paint();
  },
  updated() {
    this.paint();
  },
  beforeUnmount() {
    // Zrušený prvek nesmí nechat otevřený panel
    if (window.TickViewColorPicker.isOpenFor(this.$el)) window.TickViewColorPicker.close();
  },
  methods: {
    // Barvu vzorku předáváme CSS proměnnou (šachovnice pod ní ukáže průhlednost)
    paint() {
      if (this.$refs.fill) this.$refs.fill.style.setProperty("--tv-swatch", this.value || "transparent");
    },
    // Klik otevře panel; druhý klik na stejné tlačítko ho zavře
    toggle() {
      const picker = window.TickViewColorPicker;
      if (picker.isOpenFor(this.$el)) {
        picker.close();
        return;
      }
      this.open = true;
      picker.open(
        this.$el,
        this.value,
        (color) => this.$emit("update:value", color),
        () => {
          this.open = false;
        }
      );
    },
  },
};
