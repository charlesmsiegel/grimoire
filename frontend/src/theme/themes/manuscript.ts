import type { Theme } from "../types";

const manuscript: Theme = {
  name: "manuscript",
  label: "MANUSCRIPT",
  tokens: {
    "--bg": "#14100b", "--surface": "#1c1610", "--panel": "#0f0b07",
    "--panel2": "#241c12", "--ink": "#e8dcc6", "--subtle": "#c9bb98",
    "--muted": "#8a7a5c", "--rule": "rgba(200,164,77,.5)",
    "--rule-soft": "rgba(200,164,77,.28)", "--track": "rgba(200,164,77,.16)",
    "--chrome": "#0f0b07", "--chrome-text": "#c8a44d", "--chrome-muted": "#8a7a5c",
    "--chrome-rule": "rgba(200,164,77,.35)", "--accent": "#c8a44d",
    "--on-accent": "#1a1206", "--quote": "#a8342a",
    "--page": "linear-gradient(180deg,#efe4c9,#e8dcc0)", "--page-ink": "#2c2318",
    "--page-muted": "#9a7d3f", "--banner-bg": "#2a1712", "--banner-ink": "#e0a494",
    "--disabled": "#57503f",
    "--rw": "1px", "--rw2": "1px", "--rw3": "1px",
    "--sh2": "0 2px 8px rgba(0,0,0,.5)", "--sh3": "0 3px 12px rgba(0,0,0,.5)",
    "--sh4": "0 5px 18px rgba(0,0,0,.55)", "--sh5": "0 8px 24px rgba(0,0,0,.6)",
    "--fd": "'Cormorant Garamond',serif",
    "--fb": "'EB Garamond',Georgia,serif",
    "--fm": "'EB Garamond',serif",
    "--fx": "radial-gradient(120% 90% at 50% -10%,rgba(200,164,77,.10),transparent 60%)",
    "--glow": "none",
    // compat aliases — removed in the cleanup task once no CSS references them
    "--fg": "var(--ink)", "--font-display": "var(--fd)",
    "--font-body": "var(--fb)", "--mono": "var(--fm)", "--radius": "0px",
  },
};
export default manuscript;
