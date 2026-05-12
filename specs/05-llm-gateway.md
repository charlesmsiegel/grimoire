# 05 — LLM Gateway

## Purpose

The LLM Gateway abstracts both **text completion** and **embedding** so the rest of Grimoire can call models without knowing which provider answers. Two parallel plugin protocols live behind the same gateway because they share concerns (retries, routing, fallback, observability) and many providers offer both (e.g., OpenAI does completions + embeddings).

Concretely:

- **LLM providers** answer prompts: `complete(prompt) -> response`. Cloud (Anthropic, OpenAI) or local (llamacpp, Oobabooga).
- **Embedding providers** turn text into vectors: `embed(texts) -> vectors`. Local (sentence-transformers) or cloud (OpenAI embeddings, Cohere).

Both are *plugins* in the shallow-adapter sense — see `15-plugins.md`. The Gateway is the consumer.

## Responsibilities

- Discover LLM and embedding providers from the Plugins module
- Apply per-task routing: main turn, drift-check, extractor, NPC-tick, summary, embedding for posts, embedding for characters, etc.
- Build provider-specific API requests
- Stream responses; expose a uniform async iterator
- Track token usage and cost (where the provider reports it)
- Implement retries, timeouts, fallback to alternate provider on failure
- Cache embeddings to avoid recomputing identical text
- Log every request to the audit trail

## Non-responsibilities

- Does not assemble prompts (Context Builder does)
- Does not parse output (Extractor does)
- Does not store campaign state (State Store does)
- Does not provide images (ImageGen does)
- Does not implement providers (plugins do)

## LLM provider protocol

Implemented by `llm_provider` plugins; defined here for the consumer side.

```python
class LLMProvider(Protocol):
    id: str
    name: str
    capabilities: ProviderCapabilities

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]: ...
    async def list_models(self) -> list[ModelInfo]: ...

    # Optional
    async def estimate_tokens(self, text: str) -> int: ...
    async def health_check(self) -> HealthStatus: ...

@dataclass
class CompletionRequest:
    model: str
    system: Optional[str]
    messages: list[Message]                # role: user|assistant; content: str
    max_tokens: int
    temperature: float
    stop_sequences: list[str]
    metadata: dict                          # provider-specific extras

@dataclass
class CompletionResponse:
    text: str
    model: str
    finish_reason: str                     # 'stop', 'length', 'content_filter', 'tool_use'
    usage: TokenUsage                      # input, output, total
    raw: dict                              # provider's raw response for debugging
    cost_estimate_usd: Optional[float]
    latency_ms: int

@dataclass
class CompletionChunk:
    delta: str                             # token(s)
    is_final: bool
    usage: Optional[TokenUsage]            # populated on final chunk
```

## Embedding provider protocol

```python
class EmbeddingProvider(Protocol):
    id: str
    name: str
    model_id: str                          # e.g., 'all-mpnet-base-v2', 'text-embedding-3-large'
    dimensions: int                        # vector length

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def health_check(self) -> HealthStatus: ...
```

The Gateway batches embedding requests (provider-specific batch sizes), caches results by text+model hash, and returns vectors in input order.

## Routing

A campaign's `model_routing` config maps tasks to provider+model:

```yaml
model_routing:
  # LLM tasks
  main: anthropic.claude-opus-4-7
  drift_check: anthropic.claude-haiku-4-5
  extractor: anthropic.claude-haiku-4-5
  npc_tick: anthropic.claude-haiku-4-5
  scene_summary: anthropic.claude-haiku-4-5
  running_summary: anthropic.claude-haiku-4-5
  validation: anthropic.claude-haiku-4-5

  # Embedding tasks
  posts: sentence-transformers.all-mpnet-base-v2
  scenes: sentence-transformers.all-mpnet-base-v2
  characters: sentence-transformers.all-mpnet-base-v2
  lore: sentence-transformers.all-mpnet-base-v2
  facts: sentence-transformers.all-mpnet-base-v2
```

The Gateway parses `provider.model` references, looks up the provider via the Plugins module, and dispatches.

Routing can be set per-campaign (in `campaign.yaml`) or globally (in app config); per-campaign overrides global.

Fallback: if a primary route fails (after retries), the gateway tries an alternate (declared as `fallback:` in the routing config). Useful for cloud → local fallback when the network is down.

## Retries and timeouts

Per-request:

```python
@dataclass
class RetryPolicy:
    max_retries: int = 3
    initial_delay_ms: int = 500
    backoff_factor: float = 2.0
    retry_on: list[type] = [TimeoutError, RateLimitError, TransientError]

@dataclass
class TimeoutPolicy:
    total_seconds: float = 120
    first_token_seconds: float = 30        # for streaming
```

Permanent errors (auth, content filter, invalid request) don't retry; surfaced to the caller.

## Token tracking

Every completion is recorded:

```sql
CREATE TABLE llm_requests (
  id TEXT PRIMARY KEY,
  campaign_id TEXT,
  branch_id TEXT,
  turn_id TEXT,
  task TEXT,                              -- 'main', 'drift_check', 'extractor', ...
  provider_id TEXT,
  model TEXT,
  request_payload JSON,
  response_text TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  cost_usd REAL,
  latency_ms INTEGER,
  finish_reason TEXT,
  error TEXT,
  created_at TIMESTAMP
);
```

Surfaced in the Observability module and per-campaign settings.

## Streaming

For interactive turns the Gateway exposes:

```python
async def stream(self, task: str, request: CompletionRequest, campaign_id: str) -> AsyncIterator[CompletionChunk]:
    provider = self._resolve(task, campaign_id)
    async for chunk in provider.stream(request):
        yield chunk
        # also: count tokens incrementally, push to observability
```

The Orchestrator pushes chunks to the Frontend via WebSocket.

## Embedding cache

Embedding the same text twice (e.g., re-indexing) is wasted work. The Gateway caches by (text_hash, model_id):

```sql
CREATE TABLE embedding_cache (
  text_hash TEXT NOT NULL,
  model_id TEXT NOT NULL,
  vector BLOB NOT NULL,
  created_at TIMESTAMP,
  PRIMARY KEY (text_hash, model_id)
);
```

Lookup is `SELECT vector FROM embedding_cache WHERE text_hash = ? AND model_id = ?`. Miss → call provider, insert.

Cache eviction: LRU by `last_used_at` (added on lookup hit). Configurable size cap.

## Gateway interface

```python
class LLMGateway(Protocol):
    # LLM
    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: Optional[str] = None,
    ) -> CompletionResponse: ...

    async def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: Optional[str] = None,
    ) -> AsyncIterator[CompletionChunk]: ...

    # Embedding
    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: Optional[str] = None,
    ) -> list[list[float]]: ...

    # Introspection
    async def list_llm_providers(self) -> list[LLMProvider]: ...
    async def list_embedding_providers(self) -> list[EmbeddingProvider]: ...
    async def list_routes(self, campaign_id: Optional[str] = None) -> dict[str, str]: ...
    async def set_route(self, task: str, route: str, campaign_id: Optional[str] = None) -> None: ...

    # Estimation
    async def estimate_tokens(self, text: str, provider_id: Optional[str] = None) -> int: ...
    async def estimate_cost(self, task: str, request: CompletionRequest) -> Optional[float]: ...

    # Health
    async def health_check(self, provider_id: str) -> HealthStatus: ...
    async def health_check_all(self) -> dict[str, HealthStatus]: ...
```

## Provider examples

### Anthropic (cloud LLM)

```python
class AnthropicLLMProvider:
    id = "anthropic"
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=True,
        vision=True,
        max_context=200_000,
    )

    def __init__(self, config: dict):
        import anthropic
        self.client = anthropic.AsyncAnthropic(
            api_key=config["api_key"],
            base_url=config.get("base_url", "https://api.anthropic.com"),
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        response = await self.client.messages.create(
            model=request.model,
            system=request.system,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            stop_sequences=request.stop_sequences,
        )
        return CompletionResponse(
            text=response.content[0].text,
            model=response.model,
            finish_reason=response.stop_reason,
            usage=TokenUsage(response.usage.input_tokens, response.usage.output_tokens),
            raw=response.model_dump(),
            cost_estimate_usd=self._estimate_cost(response.usage, response.model),
            latency_ms=...,
        )

    async def stream(self, request: CompletionRequest):
        async with self.client.messages.stream(...) as stream:
            async for chunk in stream:
                yield CompletionChunk(delta=chunk.delta, is_final=False)
            yield CompletionChunk(delta="", is_final=True, usage=...)
```

### llamacpp (local LLM)

```python
class LlamaCppLLMProvider:
    id = "llamacpp"

    def __init__(self, config: dict):
        from llama_cpp import Llama
        self.llama = Llama(model_path=config["model_path"], n_ctx=config.get("n_ctx", 8192))

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        prompt = self._to_text(request)
        out = self.llama(prompt, max_tokens=request.max_tokens, temperature=request.temperature)
        return CompletionResponse(text=out["choices"][0]["text"], ...)
```

### sentence-transformers (local embedding)

```python
class SentenceTransformersEmbeddingProvider:
    id = "sentence-transformers"
    model_id: str
    dimensions: int

    def __init__(self, config: dict):
        from sentence_transformers import SentenceTransformer
        self.model_id = config.get("model", "all-mpnet-base-v2")
        self.model = SentenceTransformer(self.model_id)
        self.dimensions = self.model.get_sentence_embedding_dimension()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Run in thread pool to avoid blocking
        embeddings = await asyncio.to_thread(self.model.encode, texts, batch_size=32)
        return embeddings.tolist()
```

### OpenAI embeddings (cloud)

```python
class OpenAIEmbeddingProvider:
    id = "openai-embeddings"
    model_id: str
    dimensions: int

    def __init__(self, config: dict):
        import openai
        self.client = openai.AsyncOpenAI(api_key=config["api_key"])
        self.model_id = config.get("model", "text-embedding-3-large")
        self.dimensions = config.get("dimensions", 3072)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(model=self.model_id, input=texts)
        return [item.embedding for item in response.data]
```

## Health monitoring

The Gateway runs `health_check()` periodically on active providers. UI shows green/yellow/red. Failed providers fall back to alternates if configured.

## Configuration

```yaml
llm_gateway:
  default_llm_route: anthropic.claude-opus-4-7
  default_embedding_route: sentence-transformers.all-mpnet-base-v2

  retries:
    max_retries: 3
    initial_delay_ms: 500
    backoff_factor: 2.0

  timeouts:
    total_seconds: 120
    first_token_seconds: 30

  embedding_cache:
    enabled: true
    max_entries: 100_000
    eviction: lru

  observability:
    log_all_requests: true
    log_response_text: false              # privacy default
    log_input_tokens: true
```

## Events emitted

- `llm_request_started` (task, provider, model)
- `llm_response_received` (with usage and latency)
- `llm_request_failed` (with error)
- `embedding_request_started` / `embedding_response_received`
- `provider_health_changed` (id, old → new status)

## Open questions (deferred)

- **Cross-provider routing within a single turn.** Use a cheap model for the first draft, then a high-quality model for the rewrite? v2.
- **Reasoning models.** Some providers expose explicit "thinking" tokens or reasoning steps. v2 might surface these in the audit trail.
- **Multi-modal inputs.** Sending images to vision-capable models. The protocol allows it via `metadata`; first-class support is v2.
- **Cost budget enforcement.** Pause turns when a per-campaign budget is exceeded. v2.
- **Provider auto-selection.** Pick the best route based on task + cost + latency targets. v2.
- **Tool use.** Providers that support function calling. v2; useful for mechanics integration but not required for v1.
