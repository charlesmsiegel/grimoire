import type { Theme } from "../types";

/** The default look, derived from the retired `astral` theme.
 *
 *  Token *names* are inherited from the three-theme era so `index.css` did not
 *  have to be renamed line by line when the themes collapsed; only the values
 *  moved. The redesign's names (`--line`, `--line-strong`, `--alert`) are the
 *  ones new work should reach for — the older `--rule` / `--rule-soft` /
 *  `--quote` are kept as aliases of the same values, not as a second palette.
 *
 *  `--muted` is the contrast floor for labels. Do not go lighter. */
const dark: Theme = {
  name: "dark",
  label: "DARK",
  tokens: {
    // --- the redesign's names ---
    "--bg": "#08070f",
    "--panel": "#0a0912",
    "--surface": "#0e0d18",
    "--ink": "#d7d9ee",
    "--subtle": "#9a9cc0",
    "--muted": "#7d80a8",
    "--accent": "#6fe0da",
    // The pressed state of an accent button, and accent text that needs to
    // sit on a panel. On dark, pressing lightens.
    "--accent-strong": "#8bece7",
    "--alert": "#e77fce",
    "--line": "rgba(120,190,220,.22)",
    "--line-strong": "rgba(120,190,220,.30)",

    // --- inherited aliases, same values ---
    "--panel2": "#131120",
    "--rule": "rgba(120,190,220,.30)",
    "--rule-soft": "rgba(120,190,220,.22)",
    "--track": "rgba(120,190,220,.12)",
    "--chrome": "#0a0912",
    "--chrome-text": "#6fe0da",
    "--chrome-muted": "#5b5e82",
    "--chrome-rule": "rgba(120,190,220,.28)",
    "--on-accent": "#041014",
    "--quote": "#e77fce",
    "--on-quote": "#041014",
    "--page": "#0b0a15",
    "--page-ink": "#d7d9ee",
    "--page-muted": "#7d80a8",
    "--banner-bg": "#241021",
    "--banner-ink": "#e77fce",
    "--disabled": "#3a3d5c",

    // --- one shadow of each kind, not a ramp ---
    "--shadow-pop": "0 16px 40px rgba(0,0,0,.7)",
    "--shadow-glow": "0 0 20px rgba(111,224,218,.22)",

    // --- type ---
    "--fd": "'Cinzel',serif",
    "--fb": "'Spectral',Georgia,serif",
    "--fm": "'Space Mono',monospace",

    // The desk's starfield. Light mode has neither this nor the text glow.
    "--fx":
      "radial-gradient(1px 1px at 20% 30%,rgba(120,200,220,.5),transparent)," +
      "radial-gradient(1px 1px at 70% 20%,rgba(200,120,220,.4),transparent)," +
      "radial-gradient(1px 1px at 45% 70%,rgba(120,200,220,.35),transparent)," +
      "radial-gradient(1px 1px at 85% 60%,rgba(160,160,240,.4),transparent)," +
      "radial-gradient(90% 60% at 50% -5%,rgba(90,120,200,.14),transparent 70%)",
    "--glow": "0 0 12px rgba(79,214,208,.45)",
  },
};
export default dark;
