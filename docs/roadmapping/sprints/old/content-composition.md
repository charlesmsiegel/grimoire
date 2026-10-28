# Content Composition and Worldbuilding

**Priority:** early scoped design prototype alongside World Resolution; implementation follows demonstrated authoring benefit.

## Goal

Author one fully recognizable setting and **reuse genuinely common definitions** in other worlds without copying thousands of entries. A campaign should select its setting, one mechanics module or none, and optional compatible content without turning every imported item or creature into prompt text.

## Work to plan

- Keep defining setting content (important people, factions, regions, lore, history and places) **owned by its world**. A world may be self-contained. Do not arbitrarily split Faerûn into mandatory character, location and faction packages.
- Support optional **referenced catalogs** (shared D&D bestiary, ordinary equipment) and separately maintained supplements/creator card collections; references are not immediate copies into every new setting.
- Allow explicit include/exclude choices, specialized variants and relevant search/discovery of large catalogs. Thousands of available records must not expand routine narration context or latency.
- Support a world's recommended/default mechanics while preserving each campaign's independent choice of one mechanics module or **none**. Mechanics-specific stats may remain stored for descriptive/reference use; incompatible sheets must not be silently adjudicated under different rules.
- Imported portrayals of existing people (e.g. a Chub Drizzt) must remain distinct until an explicit match, alternate-portrayal choice or targeted overlay is made. Local card-specific greeting/scenario instructions are not automatically canonical world facts.
- Separate shared reusable definitions, world-local specializations, campaign actor instances, character art versions, and mechanical representations. Avoid a universal Goblin/Dragon/Longsword ontology based on a shared label.
- Distinguish selecting optional additions for one campaign, reusable personal presets and making a genuinely forked world. Repo partitioning is a publication decision, not necessarily a type/identity boundary.
- Define dependency consistency, cache invalidation, source upgrades, missing imports, version constraints and transportable human-editable manifests.

## Proof before selecting architecture

Build small invented **Faerûn, Krynn and Athas** collections with a shared bestiary/equipment catalog. Exclude an inappropriate item from Athas; add a Faerûn-specific goblin; enable an optional creator character set for one campaign; keep multiple Drizzt portrayals separate; switch mechanics to none; edit a catalog and show why a divergent campaign record isn't overwritten. Measure authoring friction, storage duplication and prompt costs, not just purity of the model.

## Dependencies and boundaries

[World Resolution](world-resolution.md) owns qualified identity, lookup, provenance and stable resolved views. [Content Ecosystem](content-ecosystem.md) owns distribution/import packaging; [World Maintenance](world-maintenance.md) owns reviewable integration edits. Physical Git repositories can publish worlds, libraries or private cards without defining runtime ontology.

## Open questions

- Are World and Content Pack separate user-visible/storage types, or is a World a selectable role for a generic collection?
- Do dependencies form a shallow set of libraries, a constrained DAG of catalogs/supplements, or something more general?
- Are add-ons selected per campaign, saved world preset, personal profile, or multiple scopes?
- Should a mechanics choice automatically offer compatible generic catalogs? How are missing mechanical representations represented?
- How are includes/excludes and explicit specializations/overrides authored, validated and versioned?
- Is a user's alternate portrayal a different entity, an adaptation link, or a replaceable view of one character? What is the UI for that choice?
- Which portions of a character card (greeting, scenario, lorebook, voice) become persistent world knowledge versus contextual play material?
- How do pack pinning, upgrades, removal, licenses and provenance work without rewriting shared authored files?
