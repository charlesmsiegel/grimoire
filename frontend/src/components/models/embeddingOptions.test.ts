import {
  blockOf, dimensionsTyped, embeddingForm, mismatchLine, optionsSummary, sameBlock, suggestFor,
} from "./embeddingOptions";

test("a form round-trips a stored block, and sends only its mode's fields", () => {
  const block = { input: "param" as const, param_field: "task", query_value: "retrieval.query",
                  document_value: "retrieval.passage", dimensions: 1024 };
  expect(blockOf(embeddingForm(block))).toEqual(block);
  const switched = { ...embeddingForm(block), input: "prefix" as const,
                     document_prefix: "passage: " };
  expect(blockOf(switched)).toEqual({ input: "prefix", document_prefix: "passage: ",
                                      dimensions: 1024 });
  expect(blockOf(embeddingForm(null))).toEqual({});
  expect(blockOf({ ...embeddingForm({ dimensions: 256 }), dimensions_field: "output_dimension" }))
    .toEqual({ dimensions: 256, dimensions_field: "output_dimension" });
});

test("blocks compare by what they send", () => {
  expect(sameBlock({ input: "prefix", query_prefix: "q" }, { query_prefix: "q", input: "prefix" }))
    .toBe(true);
  expect(sameBlock({}, { dimensions: 4 })).toBe(false);
});

test("a typed width is a whole number or nothing", () => {
  const form = embeddingForm({});
  expect(dimensionsTyped(form)).toBe(true);
  expect(dimensionsTyped({ ...form, dimensions: "512" })).toBe(true);
  expect(dimensionsTyped({ ...form, dimensions: "512.5" })).toBe(false);
  expect(dimensionsTyped({ ...form, dimensions: "0" })).toBe(false);
});

test.each([
  ["nomic-ai/nomic-embed-text-v1.5", "prefix", "search_query: "],
  ["intfloat/multilingual-e5-large", "prefix", "query: "],
  ["BAAI/bge-small-en-v1.5", "prefix", "Represent this sentence for searching relevant passages: "],
  ["Qwen/Qwen3-Embedding-0.6B", "prefix",
   "Instruct: Given a passage of a story, retrieve the lore, records or images it concerns\nQuery: "],
  ["google/embeddinggemma-300m", "prefix", "task: search result | query: "],
])("%s suggests its published prefixes", (model, input, query) => {
  const got = suggestFor(model);
  expect(got?.fill.input).toBe(input);
  expect(got?.fill.query_prefix).toBe(query);
});

test("request-field conventions, and no suggestion for an unknown model", () => {
  expect(suggestFor("jina-embeddings-v3")?.fill).toMatchObject(
    { input: "param", param_field: "task", document_value: "retrieval.passage" });
  expect(suggestFor("voyage-3.5")?.fill).toMatchObject({ input: "param", param_field: "input_type" });
  expect(suggestFor("text-embedding-3-small")).toBeNull();
});

test("the summary says what is sent", () => {
  expect(optionsSummary(null)).toBeNull();
  expect(optionsSummary({})).toBeNull();
  expect(optionsSummary({ input: "prefix", query_prefix: "q", document_prefix: "d",
                          dimensions: 512 })).toBe("query/document prefixes, 512 dimensions");
  expect(optionsSummary({ input: "prefix", query_prefix: "q" })).toBe("a query prefix");
  expect(optionsSummary({ input: "param", param_field: "task", query_value: "q",
                          document_value: "d" }))
    .toBe("query/document in `task`, two requests per recall");
});

test("a width mismatch is said with both widths", () => {
  expect(mismatchLine({ requested_dims: 512, returned_dims: 1536 }))
    .toBe("requested 512, test returned 1536: this endpoint ignores `dimensions`");
  expect(mismatchLine({})).toBeNull();
});
