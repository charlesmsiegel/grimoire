Source: [view.py](../../../../../backend/src/grimoire/store/regex/view.py), [stream.py](../../../../../backend/src/grimoire/store/regex/stream.py).

## Output processing: stored text is raw

`store/regex/` runs find-and-replace rules over transcript text, and the transcript
itself stays what was written: a rule changes what is *shown* or *sent*.

- **Four levels, one order**: connection → global → world → campaign, each a JSON
  rule file. Only the world and campaign files carry `off`, a list of inherited
  rule ids; the global and connection files sit under nothing and `validate_doc`
  refuses one there. `off` is a switch, never an edit — the only way to change an
  inherited rule is to switch it off and add your own. A stale `off` id (its rule
  deleted upstream) is pruned on write, not refused.
- **LLM readers of transcript text go through `store.regex.view.view`** (phase
  `prompt`). `test_regex_prompt_guard.py` fails a reader that never asks for it.
- **Display runs on the server**, scene reads and `display` stream frames
  (`store/regex/stream.py`) alike, so one Python `re` dialect governs the screen,
  the test pane and the prompt. Markdown, HTML, text and EPUB exports use display
  text; only the JSON export stays raw.
- **A stored rewrite is opt-in** (`rewrite_stored`) and happens only as text lands,
  never over an existing transcript. The original is recorded per scene *identity*
  under `<campaign>/rewrites/`; Restore is validated against that record and
  refused as stale if the text has moved past it. A rewrite that would empty the
  text is never stored. The SillyTavern importer refuses what it cannot translate
  rather than guessing.
