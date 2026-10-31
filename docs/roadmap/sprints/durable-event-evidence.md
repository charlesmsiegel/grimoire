# Accepted events, observations and actor evidence

**Outcome:** Canonical events are different from actor-specific observations, reports, hypotheses and false beliefs.

## Scope and first proof
Record a private event, explicit witness and a mistaken interpretation; verify that the absent actor sees neither secret.

## Subsequent milestones and moonshot
Retcon-aware evidence references, deterministic materialized state projections and durable write/recovery.

## Independence and interfaces
Events/evidence persistence, not generation; requires native store and knowledge contract.

## Proving scenario
The model cannot treat a reported accusation as established truth for an uninformed NPC.

## Wave 2 handoff: retain 012's evidence meaning

Implement the persisted form of **012's access/evidence/claim contract**, including source, observer, confirmed receipt, communication channel, scene/turn ordering, uncertainty, and private audience. Persist canonical accepted event facts separately from individual actor observations, false reports or beliefs. A private witness's evidence must not leak via the director's record or a graph-derived projection; use typed source/instance identity and generic 010 revisioned envelopes.

The first proof includes a false report **with proof of delivery** and a bystander that never received it. A normal reader cannot silently promote rumor into fact. Ownership: 020 persists evidence; 012 owns its semantics; 017 graph traverses it; 032 later handles actual transmission. Keep independent Wave 3 ownership (no dependence on 017's unfinished graph writer).

## Evidence is owned by play scope, not a world

A 107 lightweight chat has a stable campaign namespace even without an authored world; event and evidence stores must accept that namespace and its chat/scene IDs. Character and scenario links identify **authoring sources**, not a grant to know the entire chat transcript. A newly added NPC begins with only explicitly introduced/delivered knowledge and cannot be assumed to have perceived earlier accepted events. Group chats and later world promotion retain their own source/actor/history provenance. [Chat model](../CHAT_MODEL.md).

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
