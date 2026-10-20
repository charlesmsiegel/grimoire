# Store operations

## Work through the store, not the filesystem

Write through `grimoire.store`. Hand-writing markdown into the store looks easy and is a
reliable way to produce files the app then disagrees with — slug collisions, frontmatter
quoting, `{{char}}` baking, and newline translation are all already solved:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(REPO) / "backend" / "src"))

from grimoire.store import characters, entities, greetings, tags, voice_anchors
from grimoire.store.paths import home

root = home() / "worlds" / world_id
```

- `entities.create_entity(root, kind, name, body=..., keys=..., owners=...)` —
  `kind` is one of `entities.ENTITY_KINDS`; returns the slug it allocated.
- `entities.update_entity(root, kind, eid, body=...)`, `entities.list_entities(root, kind)`,
  `entities.read_entity(root, kind, eid)`, `entities.delete_entity(root, kind, eid)`.
- `greetings.create_greeting(root, name, character, version, body=..., present=[...],
  requires_tags=[...])` — bakes `{{char}}` at write time, which is why you should not
  hand-roll this.
- `greetings.set_edges(root, gid, leads_to=[...])` for plot-map chains.
- `tags.add_tag(root, display_name)` → tag id; `tags.read_tags(root)`.
- `voice_anchors.write(root, char_id, text)`; `voice_anchors.anchorless_ids(root, ids)`.
- `characters.read_card(root, cid, vid)` → the card dict; `characters.list_characters(root)`.

`atomic.write_text` deliberately keeps the platform's newline translation, so a store written
on Windows stays CRLF and you never think about it. Bypassing it with explicit `newline=`
rewrites whole files and buries your actual edit in the diff.
