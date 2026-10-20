# Models and providers

[Documentation](../README.md) · [Context and memory](context.md)

## Connect a provider, then assign a model

A **provider** is an endpoint or account. A **model selection** combines a
provider, model ID and optional sampling preset. Saving a provider does not
assign it to every job.

Open **Settings → Inference → Providers** to create the provider. Then open
**Models** to assign roles. The pages are also available at `/providers`,
`/models` and `/models/edit` in the running app.

## Supported providers

| Adapter / preset | What to configure |
|---|---|
| OpenRouter | API key, then a model from its catalog or by ID |
| Anthropic API | API credentials and the chosen model |
| Claude subscription | Optional `claude` backend extra and local authenticated integration |
| OpenAI-compatible | Base URL, optional API key and a model ID |

The preset list also includes OpenAI, z.ai, z.ai Coding Plan, Ollama and LM Studio.
A preset fills in endpoint and billing facts; model capabilities can still vary.
The Claude subscription adapter does not provide vision or embedding support
here. Its integration is a desktop dependency.

## Roles and routes

| Role | Default responsibility | When unset |
|---|---|---|
| Primary | Scene prose | Configure a usable selection before generating |
| Fast | Absorb, summaries and other background work | Inherits Primary |
| Decision | Closed questions, including speaker and continuity decisions | Inherits Fast |
| Embedding | Vector recall, semantic search and related matching | Disabled |

Primary, Fast and Decision can each specify a fallback. A fallback known not to
meet a route's requirements is reported rather than sent blindly.

Under **Advanced**, route overrides select another role or a specific model.
Campaign settings can override the three generative roles and campaign routes.
Embedding is global, so the library's embedding work uses a consistent vector
space.

## Local servers

1. Start your model server and load the desired model.
2. Add an Ollama, LM Studio or Custom provider.
3. Enter its OpenAI-compatible base URL, including an API prefix such as `/v1`
   when that is what the server exposes.
4. Refresh its catalog or enter the exact model ID it serves.
5. Assign the provider/model to a role, save, and run a model test if needed.

The shipped Ollama preset uses `http://localhost:11434/v1`; LM Studio uses
`http://localhost:1234/v1`. These are preset values, not discovery of your server.
Use your actual address and port.

A phone's `localhost` is the phone. For a model on another computer, use that
computer's reachable network address. See [Android](../reference/android.md#using-a-model-server-on-another-machine).

## Check connectivity and capabilities

**Check** and **Test model** answer different questions. A connection check can
verify that a provider responds without proving that a model accepts the
generation, image or structured request you intend to send. A server without a
model-list endpoint may fail a catalog-based check while accepting generation.

Model tests show a preview and require confirmation before sending probes.
A Claude subscription health check also generates and asks first. Tests can
incur normal provider costs.

Model pages show known capabilities and their sources. Unknown means no verified
answer is available. A failed probe is not proof that a capability is absent.
A known incompatibility can prevent a request before it is sent.

If an endpoint rejects the arrangement of system messages, inspect the model's
**Prompt post-processing** setting; Strict is available for stricter message
requirements. Set vision, prefill and other facts to what the model supports.

## Sampling presets and optional work

Sampling presets group supported controls such as temperature and reasoning
settings. They are separate from credentials. Read the effective controls rather
than assuming every adapter accepts every field.

Play decisions that add a planning call or a decision tool default off. Speaker
sampling defaults on and uses the speaker decision already being asked. History
reranking and query planning default off; historical recall is disabled at a
recall depth of zero. Enable these deliberately when the extra work helps.

Changing an active embedding selection or its document-side options can require
re-embedding, and the relevant save asks for confirmation. A query-only option
change and a document-space change are different operations.

## Read the cost figures correctly

| Figure | Meaning |
|---|---|
| Billed | A provider reported an actual charge; campaign budgets measure this |
| Subscription equivalent | A provider reported an API-equivalent value for subscription activity |
| Modelled | Grimoire calculated a value using configured rates |

Do not add the columns to infer spend. The monthly chart's **Estimated total**
is a specifically labelled projection, not a billing total or budget value.
An unpriced call makes a total incomplete; an absent price is not free usage.

For unpriced generative and embedding calls, enter model rates on the provider's
model page or in the pricing table. Reported prices take precedence. Native
decisions are not modelled at chat token rates. Counts estimated locally are
labelled too.

## Implementation references

- [Provider presets](../../backend/src/grimoire/store/inference/providers.py) and [provider records](../../backend/src/grimoire/store/llm_connections.py)
- [Resolution](../../backend/src/grimoire/store/inference/resolve/) and [settings](../../backend/src/grimoire/store/inference/settings.py)
- [Provider UI](../../frontend/src/routes/ProvidersView.tsx) and [model UI](../../frontend/src/routes/ModelsView.tsx)
- [Usage](../../backend/src/grimoire/store/usage/), [pricing](../../backend/src/grimoire/store/pricing.py) and [play decisions](../../backend/src/grimoire/store/play_decisions.py)
