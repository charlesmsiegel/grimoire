export type Model = { id: string; name: string; prompt: string; completion: string };

const MODELS_URL = "https://openrouter.ai/api/v1/models";

export async function fetchModels(): Promise<Model[]> {
  const res = await fetch(MODELS_URL);
  if (!res.ok) throw new Error(`failed to load models: ${res.status}`);
  const body = (await res.json()) as { data: any[] };
  return body.data
    .map((m) => ({
      id: m.id,
      name: m.name,
      prompt: m.pricing?.prompt ?? "0",
      completion: m.pricing?.completion ?? "0",
    }))
    .sort((a, b) => a.id.localeCompare(b.id));
}

function compact(n: number): string {
  if (n >= 1e6) return strip(n / 1e6) + "M";
  if (n >= 1e3) return strip(n / 1e3) + "K";
  return String(Math.round(n));
}

function strip(x: number): string {
  return String(Math.round(x * 10) / 10);
}

export function tokensPerDollar(price: string): string {
  const n = Number(price);
  if (!isFinite(n) || n === 0) return "Free";
  return compact(1 / n);
}

export function priceLabel(model: Model): string {
  if (Number(model.prompt) === 0 && Number(model.completion) === 0) return "Free";
  return `${tokensPerDollar(model.prompt)} / ${tokensPerDollar(model.completion)} tok/$`;
}
