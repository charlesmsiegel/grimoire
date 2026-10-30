# Portable Content Packages and Sharing

**Outcome:** Publish and consume worlds, rules, cards and supplements as inspectable offline-friendly artifacts.

## Scope
- Describe package contents, ownership, versions, dependencies, licenses and compatibility in editable manifests.
- Export with necessary images and expressions while excluding private campaign data unless deliberately selected.
- Validate import upgrades and malicious or incompatible sources before applying.

## Milestones
1. Round-trip one world and one shared catalog as portable packages.
2. Validate missing references, license metadata and an upgrade conflict.
3. Support creator distribution through local files or repos independent of an app marketplace.

## Moonshot
A federated discovery ecosystem with trust and attribution, but never a dependency for private local-world authoring.

## Proving scenario
An exported creator add-on installs into a second library with provenance intact and no copied private campaign scenes.

## Independence and interfaces
Needs only a minimal source manifest; marketplace hosting, commercial policy and auto-updaters remain optional.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Export and re-import preserve source-qualified references byte-identically in the [identity](../../IDENTITY.md) text forms, including pins, links and broken references (002 milestone 3).

## Open questions
- What is the smallest useful package manifest?
- How are licensed/restricted assets represented?
- What trust controls are needed before remote discovery?
