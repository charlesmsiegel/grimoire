# Verify and commit library edits

## Step 10: Verify, then commit

Referential integrity is already implemented; don't reimplement it:

```bash
python - <<'PY'
import sys; from pathlib import Path
sys.path.insert(0, "backend/src")
sys.path.insert(0, ".agents/skills/populate-world-content/scripts")
from populate_world_content import verify_manifest
from grimoire.store.paths import home
print(verify_manifest(home() / "worlds" / "<world-id>"))
PY
```

That checks greeting→character, `requires_tags`→tag, `present`→character, plot-map edges→
greeting, and `owners`→entity or character. Then the check it cannot do for you:

```bash
git -C "$STORE" status --porcelain worlds/<world-id>/ | grep '^ M'
```

Every modified file should be one you deliberately edited. A list longer than your intent
almost always means something rewrote line endings or trailing bytes across a directory —
revert the strays with `git checkout --` before committing rather than shipping the churn.

Then commit in the store, in the house style its previous integration commits establish: a
subject naming the World and what arrived, a paragraph on who these characters are and how
they relate, then sections for what you merged, what you deleted and why, what you corrected,
and which judgment calls you made. The user reads that message to check your work, so the
reconciliations and corrections are the substance — not the file counts.

## Reference

- `references/house-formats.md` — the entry shapes this store uses, with invented examples
- `scripts/inspect_cards.py` — dump cards to readable text, diff their lorebook against lore
- `.agents/skills/populate-world-content/` — the bulk import this skill is the follow-up to

After writing the records, finish with [store-editing sync](../../grimoire-store-editing/references/sync/batch.md#sync-a-coherent-batch).
