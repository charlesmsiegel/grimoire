/** A model's embedding options (roadmap 01h): the form the facts panel edits,
 *  the block a save sends, the suggestions it can fill in, and the sentences
 *  the Models page and the test results say about them. The server judges
 *  every block (`facts._check_embedding`); nothing here decides what is valid
 *  beyond what the form can tell before it asks. */
import type { EmbeddingInputMode, EmbeddingOptionsBlock } from "../../api/types";

/** The field a requested width is sent in when nothing else is stated. */
export const DEFAULT_DIMENSIONS_FIELD = "dimensions";

/** The options as the form holds them: every box as typed. */
export type EmbeddingForm = {
  input: EmbeddingInputMode;
  query_prefix: string; document_prefix: string;
  param_field: string; query_value: string; document_value: string;
  /** A whole number as typed, or "" for the model's own width. */
  dimensions: string; dimensions_field: string;
};

const text = (v: unknown) => (typeof v === "string" ? v : "");

/** The form for a stored block -- or for whatever is on disk, an invalid
 *  block included, so the reader can see and fix it. */
export function embeddingForm(block: unknown): EmbeddingForm {
  const b = (block && typeof block === "object" ? block : {}) as Record<string, unknown>;
  const input = b.input === "prefix" || b.input === "param" ? b.input : "none";
  return {
    input,
    query_prefix: text(b.query_prefix), document_prefix: text(b.document_prefix),
    param_field: text(b.param_field), query_value: text(b.query_value),
    document_value: text(b.document_value),
    dimensions: typeof b.dimensions === "number" ? String(b.dimensions) : "",
    dimensions_field: text(b.dimensions_field) || DEFAULT_DIMENSIONS_FIELD,
  };
}

/** Whether the typed width is a whole number the server could take, or
 *  empty. The server names anything else in its own words. */
export function dimensionsTyped(form: EmbeddingForm): boolean {
  const typed = form.dimensions.trim();
  return typed === "" || /^[1-9][0-9]*$/.test(typed);
}

/** What a save sends for the form: only the fields its mode uses, a width
 *  only when one is typed, and `{}` for no options at all. */
export function blockOf(form: EmbeddingForm): EmbeddingOptionsBlock {
  const out: EmbeddingOptionsBlock = {};
  if (form.input === "prefix") {
    out.input = "prefix";
    if (form.query_prefix) out.query_prefix = form.query_prefix;
    if (form.document_prefix) out.document_prefix = form.document_prefix;
  } else if (form.input === "param") {
    out.input = "param";
    out.param_field = form.param_field.trim();
    out.query_value = form.query_value;
    out.document_value = form.document_value;
  }
  const typed = form.dimensions.trim();
  if (typed !== "") {
    out.dimensions = Number(typed);
    const field = form.dimensions_field.trim();
    if (field && field !== DEFAULT_DIMENSIONS_FIELD) out.dimensions_field = field;
  }
  return out;
}

/** Whether two blocks send the same thing (key order aside). */
export function sameBlock(a: EmbeddingOptionsBlock, b: EmbeddingOptionsBlock): boolean {
  const norm = (x: EmbeddingOptionsBlock) =>
    JSON.stringify(Object.entries(x).sort(([p], [q]) => p.localeCompare(q)));
  return norm(a) === norm(b);
}

/** One published convention: what a model's authors say to send. */
export type EmbeddingSuggestion = { family: string; fill: Partial<EmbeddingForm> };

/** The conventions a Suggest can fill in (spec 01h §3.5), matched against the
 *  model id. A suggestion fills the form only: nothing is applied until the
 *  reader saves (and confirms), so a later change to this table can never
 *  move anybody's vector space. */
const SUGGESTIONS: { match: RegExp; suggestion: EmbeddingSuggestion }[] = [
  { match: /nomic-embed-text/i, suggestion: { family: "nomic-embed-text", fill: {
    input: "prefix", query_prefix: "search_query: ", document_prefix: "search_document: " } } },
  { match: /(^|[/_-])(multilingual-)?e5([_-]|$)/i, suggestion: { family: "E5", fill: {
    input: "prefix", query_prefix: "query: ", document_prefix: "passage: " } } },
  { match: /bge-[a-z]+-en-v1\.5|mxbai-embed-large/i, suggestion: { family: "BGE", fill: {
    input: "prefix",
    query_prefix: "Represent this sentence for searching relevant passages: ",
    document_prefix: "" } } },
  { match: /qwen3-embedding/i, suggestion: { family: "Qwen3-Embedding", fill: {
    input: "prefix",
    query_prefix: "Instruct: Given a passage of a story, retrieve the lore, records or "
      + "images it concerns\nQuery: ",
    document_prefix: "" } } },
  { match: /embeddinggemma/i, suggestion: { family: "EmbeddingGemma", fill: {
    input: "prefix", query_prefix: "task: search result | query: ",
    document_prefix: "title: none | text: " } } },
  { match: /jina-embeddings-v3/i, suggestion: { family: "jina-embeddings-v3", fill: {
    input: "param", param_field: "task", query_value: "retrieval.query",
    document_value: "retrieval.passage" } } },
  { match: /voyage/i, suggestion: { family: "Voyage", fill: {
    input: "param", param_field: "input_type", query_value: "query",
    document_value: "document" } } },
];

/** The convention published for `model`, or null when none is known. */
export function suggestFor(model: string): EmbeddingSuggestion | null {
  return SUGGESTIONS.find(({ match }) => match.test(model))?.suggestion ?? null;
}

/** What a stated block sends, as a phrase ("query/document prefixes, 512
 *  dimensions"), or null for none. A request-field type costs a recall two
 *  requests, and says so. */
export function optionsSummary(block: EmbeddingOptionsBlock | null | undefined): string | null {
  if (!block) return null;
  const parts: string[] = [];
  if (block.input === "prefix") {
    const q = !!block.query_prefix;
    const d = !!block.document_prefix;
    parts.push(q && d ? "query/document prefixes" : q ? "a query prefix" : "a document prefix");
  } else if (block.input === "param") {
    parts.push(`query/document in \`${block.param_field ?? ""}\``);
  }
  if (typeof block.dimensions === "number") parts.push(`${block.dimensions} dimensions`);
  if (block.input === "param") parts.push("two requests per recall");
  return parts.length ? parts.join(", ") : null;
}

/** The sentence for a test whose endpoint ignored a requested width, or null
 *  for any other result. */
export function mismatchLine(r: { requested_dims?: number; returned_dims?: number }):
    string | null {
  if (typeof r.requested_dims !== "number" || typeof r.returned_dims !== "number") return null;
  return `requested ${r.requested_dims}, test returned ${r.returned_dims}: `
    + "this endpoint ignores `dimensions`";
}
