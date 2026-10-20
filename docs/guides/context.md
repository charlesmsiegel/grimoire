# Context and memory

[Documentation](../README.md) · [Models and providers](models.md)

## Inspect before tuning

Open **Context** in the scene inspector to see the prompt Grimoire would compose
now. It combines the selected characters and PC, scene setting, recent posts,
campaign memory, activated lore and other enabled sources. Sections show their
token estimates and whether budget packing omitted them.

For a specific past reply, open its entry in **Turn history**. A current context
preview cannot reconstruct what that earlier call saw after records have changed.

## Understand the memory layers

| Layer | Purpose |
|---|---|
| Recent transcript | The conversation currently being played |
| Rolling summary | A shorter account of earlier material in a long scene |
| Chronicle | Reviewed summaries of completed scenes and campaign recap |
| Dossiers and state | Campaign-specific information about the cast |
| Lore | Reusable world information activated for this situation |
| Historical recall | Selected evidence retrieved from earlier scenes |

Wrap-up maintains reviewed campaign memory. Background summaries help with long
active scenes, but are not a substitute for reviewing a completed scene's updates.

## Set a context budget

**Context budget** is a prompt-packing setting. Its default, `0`, leaves packing
unbounded; it does not automatically become the model's advertised context window.
A positive budget lets Grimoire shed lower-priority material and trim older
conversation history while protecting essential instructions and pinned content.
Divisible sections, including world info and recalled history, can shed units.

The model's known window and output limits are separate facts. Use their readout
alongside the prompt estimate when choosing a budget. A prompt composed within
your configured budget can still be rejected by a provider with different
counting or request constraints. Token counts are estimates; Android does not
ship the desktop tokenizer.

## Control lore and history

Lore activation uses entry keys and controls such as scan depth, secondary-key
logic, recursion, priority, sticky/cooldown and keep settings. Pins protect the
selected content; exclusions remove it before packing. Inspect the current
activation instead of adding an unrelated keyword to make an entry appear.

Historical recall is off when **history recall depth** is `0`, its default.
Enabling it retrieves selected older scene material. Lexical retrieval and
semantic retrieval are separate signals; embeddings require an Embedding model.
Optional reranking and query planning can add model work and default off.
Planning has no effect while historical recall is disabled.

An NPC's historical evidence is filtered for that actor. Authorship, presence and
manual knowledge overrides can determine access; summaries do not automatically
become known merely because a character appeared in a scene. The narrator and
an individual NPC therefore need not receive the same retrieved evidence.
Knowledge overrides belong in the Ledger. These filters govern supplied context,
not every inference a language model may make from it.

## Retained prompts and their limits

**Kept turn prompts** controls retention. The default is `50`; `0` disables
capture. Retention is per campaign and per capture pool, so decisions and tool
loops do not evict ordinary generation entries from their pool. Entries can
still age out; this is a debugging window rather than permanent archival storage.

A capture records Grimoire's composed messages and their breakdown. Adapter
post-processing can change the final wire format: Strict may rearrange roles,
and the Claude integration flattens messages. A prompt snapshot is not a byte
capture of the provider's HTTP request. Distinct dispatched model variants can
have their own prepared compositions, including fallback model guidance.

## Style, guidance and output rules

Style guides and response presets shape writing. Model guidance profiles under
`templates/scene/model_guidance/` are selected by supported model IDs and frozen
with the prepared prompt; they are not a generic profile inferred for every model
whose name looks similar. Inspect the actual prompt when comparing models.

Output rules operate on views of transcript text. Display and prompt processing
can differ, while stored transcript text remains raw unless a stored rewrite was
explicitly enabled. Restore operations for stored rewrites check whether the
record still matches. A display change is not evidence that old stored text was
rewritten.

## Investigate a surprising reply

1. Open its retained prompt, if available, rather than only the current preview.
2. Check which provider/model served it and whether a fallback was used.
3. Check the selected character version, PC and response settings.
4. Inspect missing, excluded or shed lore and history.
5. Correct the owning record or setting, then choose a reroll or replay suited
   to whether you want historical or rebuilt context.

For raw response capture and errors, see [Troubleshooting](troubleshooting.md#logs-and-incoming-model-responses).

## Implementation references

- [Composition](../../backend/src/grimoire/store/context/assemble/), [packing](../../backend/src/grimoire/store/context/pack.py) and [activation](../../backend/src/grimoire/store/context/activation.py)
- [Historical recall settings](../../backend/src/grimoire/store/history/settings.py) and [actor access](../../backend/src/grimoire/store/context/epistemic/)
- [Prompt records](../../backend/src/grimoire/store/prompt_log.py) and [model guidance](../../backend/src/grimoire/model_guidance.py)
- [Output processing](../../backend/src/grimoire/store/regex/) and [prompt templates](../../templates/README.md)
