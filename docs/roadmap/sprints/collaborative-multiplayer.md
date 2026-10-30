# Multiuser Campaign Collaboration

**Outcome:** Several human participants can safely control their own characters while sharing a consistent campaign.

## Scope
- Authenticate controllers, PC ownership, audience privacy and editable permissions.
- Coordinate simultaneous drafts, scene advances, approvals and state conflicts across devices.
- Preserve local-first readable files and expose offline/reconnect behavior honestly.

## Milestones
1. Prove turn exchange and role permissions between two devices.
2. Resolve simultaneous scene edits without data loss or false success.
3. Support live party sessions, asynchronous play and delegated co-GM authority.

## Moonshot
A distributed virtual tabletop with seamless text, voice and rich scene participation, still portable and playable offline.

## Proving scenario
Two controllers act on one scene; an unauthorized player cannot read a private GM note or mutate another PC's holdings.

## Independence and interfaces
Starts with ordinary local file-backed campaign and a minimal coordinator; local multi-PC drafts are related but not a prerequisite.

## Inherited from Wave 1

The [Wave 1 audit](../README.md#wave-1-status) left these with this sprint. *Obligations* are contracts it must not violate; *first-proof additions* enlarge its first slice and are listed in the roadmap row.

**Obligations**
- Each human approves only uses of grants they issued (007).
- Only the player who asked may confirm a pending destructive command, for typed and UI input as well as voice. Wave 1's confirmation is bound to its target and revision but names no speaker (008); voice attribution of who said "yes" is [Multi Speaker Table Audio](multi-speaker-table-audio.md#inherited-from-wave-1).

## Open questions
- What conflict model works beyond single-device locks?
- How is private information isolated?
- What roles require approval of an advancement?
