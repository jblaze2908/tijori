// Colour follows the entity, never its rank. Categorical slots are the validated --c1..--c8 order
// (dataviz validate_palette, dark, against --s1); anything past eight folds into neutral grey.

/** Names follow the backend's default taxonomy (docs/api.md). Stacks are drawn in slot order so neighbours
 *  are always the validated adjacent pairs. */
export const CATEGORY_SLOT: Record<string, number> = {
  Shopping: 1,
  Groceries: 2,
  "Bills & subscriptions": 3,
  "Eating out": 4,
  "Local shops": 5,
  Entertainment: 6,
  Travel: 7,
  "Card bill payment": 8,
};

export const OTHER = "var(--t3)";

// Money flows share one mapping everywhere: the first three slots validate all-pairs, and income is grey context.
export const FLOW = { spend: "var(--c1)", invest: "var(--c2)", saved: "var(--c3)", income: "var(--t3)", committed: "var(--t3)" } as const;
export const slotColor = (slot: number | undefined) => (slot && slot >= 1 && slot <= 8 ? `var(--c${slot})` : OTHER);
export const categoryColor = (c: string | null) => slotColor(c ? CATEGORY_SLOT[c] : undefined);

/** Net-worth groups in stack order: cash first, then the sheet's component keys (docs/api.md). */
export const NW_SHEET_KEYS = ["sbi", "hdfc", "fd", "stocks", "mf", "ppf", "epf", "gold", "other"];

// Sequential single-hue ramp for the spend heatmap (blue, validated --ordinal on the dark surface);
// dark mode runs dark→light so "near zero" recedes toward the surface.
export const HEAT = ["#184f95", "#256abf", "#3987e5", "#6da7ec", "#b7d3f6"];
export const HEAT_ZERO = "var(--s3)";

// Diverging pair: blue ↔ red poles with the neutral gray midpoint (validated, dark). Red = worse for you.
export const DIV_GOOD = "#3987e5";
export const DIV_BAD = "#e66767";
export const DIV_MID = "#383835";

const MONOGRAM = ["#3b5b8c", "#7a4a2c", "#2c6b55", "#6b5a24", "#6d3f5a", "#2d5e2d", "#4f4787", "#7a3b3b"];
export function monogramColor(s: string): string {
  let h = 0;
  for (const ch of s) h += ch.charCodeAt(0);
  return MONOGRAM[h % MONOGRAM.length] ?? "#303030";
}

function luminance(hex: string): number {
  const n = parseInt(hex.replace("#", ""), 16);
  const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * ch[0]! + 0.7152 * ch[1]! + 0.0722 * ch[2]!;
}
/** White or near-black, whichever contrasts more with a fill: for labels set inside a coloured mark. */
export function inkOn(hex: string): string {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) return "#fff";
  const L = luminance(hex);
  return (1.05 / (L + 0.05)) >= ((L + 0.05) / (luminance("#111111") + 0.05)) ? "#fff" : "#111";
}
