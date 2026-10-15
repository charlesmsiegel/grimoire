# Image store cleanup: independent portion of #480

The user requested #480 while holding changes that intersect #477-479.
Consolidate existing metadata rules without changing formats, image selection,
read costs or caption precedence.

## Included

- Share projection of already-scanned placements to image ids and lightweight
  name rows in image_refs. Record and library backlogs retain their own name
  policies and ordering. Helpers perform no filesystem reads.
- Share inherited-caption publication in image_descriptions: a resolving World
  placement updates its shared object, clears World and campaign legacy keys,
  and falls back to the campaign key if unconfirmed. Preserve every record-art
  guard and each caller's distinct handling of legacy art.
- Restore campaign_images.world_dir tolerance for OSError from the World
  directory lookup. Errors reading campaign.md itself still propagate.
- Lock image_subjects.write_subjects using the existing subjects-sidecar lock
  across validation and publication. Keep full-replacement sidecar semantics;
  this legacy writer does not edit objects.
- Check the greeting_images docstring against all sidecar cases, clarifying
  same-world format-1 collection members if needed. Move local imports in
  independent image-store tests to module scope.

## Held for neighboring work

- Shared collection-member resolution and double reads (#477, #478).
- Public names for helpers consumed by image_surfaces and migration (#478).
- Root-taking migration/GC mutator classification (#478, #479).
- test_has_format1_fails_closed fixture conversion: its scanners and collection
  tests are being changed by #477 and #478.
- Import cleanup inside collection, migration, GC and maintenance tests those
  issues are changing. Do not implement their performance or operational fixes.

## Contracts and proof

Use existing image_refs, assets and sidecar locks; add no dependencies.
Keep Android compatibility and acyclic module-scope imports. Tests isolate
GRIMOIRE_HOME under tmp_path and use existing placeholder names only.

Reproduce missing World-directory tolerance and the unlocked bulk subject write
with failing tests before fixing. Characterize row output, imageless refs,
missing objects, policy and order before extraction. Existing inherited-art and
record-art guard tests cover fallback and stale-save refusal. Run focused image
tests, make check and all repository Codex review gates. Link the partial PR
with Refs #480: held checklist items must not be closed by this PR.

## Theory

A placement scan supplies identity; each surface supplies its name policy.
Inherited captions describe shared pixels; unconfirmed legacy fallback belongs
to the edited campaign. Subject sidecars are whole snapshots, so all writers
must use the same publication lock. Review guard forwarding and filesystem-read
counts particularly carefully during extraction.
