export type Model = {
  id: string;
  name: string;
  context: number | null;
  prompt: string | null;
  completion: string | null;
};

const MODELS_URL = "https://openrouter.ai/api/v1/models";

export async function fetchModels(): Promise<Model[]> {
  const res = await fetch(MODELS_URL);
  if (!res.ok) throw new Error(`failed to load models: ${res.status}`);
  const body = (await res.json()) as { data: any[] };
  return body.data
    .map((m) => ({
      id: m.id,
      name: m.name,
      context: m.context_length ?? 0,
      prompt: m.pricing?.prompt ?? "0",
      completion: m.pricing?.completion ?? "0",
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

// The OpenRouter catalog is a large download and changes rarely; every mount
// of the model pickers used to re-fetch it. One copy per page load is enough.
let modelsCache: Promise<Model[]> | null = null;

export function invalidateModelsCache() {
  modelsCache = null;
}

export function getModels(): Promise<Model[]> {
  if (!modelsCache) {
    modelsCache = fetchModels().catch((err) => {
      modelsCache = null; // never cache a failure
      throw err;
    });
  }
  return modelsCache;
}

function compact(n: number): string {
  if (n >= 1e6) return strip(n / 1e6) + "M";
  if (n >= 1e3) return strip(n / 1e3) + "K";
  return String(Math.round(n));
}

function strip(x: number): string {
  return String(Math.round(x * 10) / 10);
}

export function tokensPerDollar(price: string | null): string {
  if (price == null) return "";
  const n = Number(price);
  if (!isFinite(n) || n === 0) return "Free";
  return compact(1 / n);
}

export function contextLabel(context: number): string {
  if (!context) return "";
  if (context >= 1e6) return Math.round(context / 1e6) + "M ctx";
  if (context >= 1e3) return Math.round(context / 1e3) + "K ctx";
  return context + " ctx";
}

export function priceLabel(model: Model): string {
  if (model.prompt == null || model.completion == null) return "";
  if (Number(model.prompt) === 0 && Number(model.completion) === 0) return "Free";
  return `${tokensPerDollar(model.prompt)} / ${tokensPerDollar(model.completion)} tok/$`;
}
