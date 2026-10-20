# Inputs and campaign setup

## Inputs

- A directory of raw logs (Claude.ai exports: `## User` / `## Claude` turns).
- Optionally, an old campaign-skill `.skill` archive (a zip) — unzip it and read `SKILL.md` +
  `state/active_characters/*.md` for roster/premise context and, later, for verification. Never
  copy its `state/` prose directly into grimoire — every fact must come from re-running absorb on
  the actual transcript.
- A target grimoire World id, and a campaign name.
- Any player character (PC) referenced via `{"kind": "pcs", "id": "..."}` in a scene's
  `characters` field must resolve in the campaign or its World. Current campaigns
  inherit through the [overlay](../../../../backend/src/grimoire/store/overlay.py);
  `resolve_version` in the ingest script uses that union. Add missing PCs through
  the app before ingestion rather than assuming a physical campaign copy exists.

## Workflow

1. **Setup:** If the user hasn't already told you which campaign (e.g. "continue campaign X" or
   "start a new one called Y"), don't assume — ask. List existing campaigns first (each
   `<grimoire home>/campaigns/*/campaign.md` has `name`/`world` in its frontmatter; AGENTS.md
   documents how the home directory resolves), then offer the user:
   continue one of the existing campaigns (call out any already on the target World by name), or
   start a fresh one (you'll need a name). Once that's settled:
   `$PY backend/scripts/ingest_scene.py setup --world <world-id> --name "<Campaign Name>"`
   This is idempotent — it finds-or-creates by exact name+world match, so re-running it for an
   existing campaign is always safe. Prints the campaign id — use it for every following step.
