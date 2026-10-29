# Tool Permissions and Untrusted Content Boundaries

> **V2 scope correction:** Scope pairing keys, sync transport and migration agents explicitly; imported cards must never execute instructions, expose provider secrets or write into the V1 library. See the [V2 ordered roadmap](../README.md) for hard prerequisites and delivery wave; older references to existing V1 systems are porting targets, not the V2 starting baseline.


**Outcome:** Keep imported worlds, model outputs and autonomous tools from acquiring authority they were not granted.

## Scope
- Separate inspect, propose, apply and execution permissions for tools and extensions.
- Validate paths, scopes, network/provider exposure and external content before privileged operations.
- Provide readable consent/permission policies and audit of denied or allowed operations.

## Milestones
1. Threat-model imported Markdown, cards, rules scripts and worker inputs.
2. Define least-privilege capabilities for one existing tool workflow.
3. Test prompt-injection, path traversal and secret-exposure attempts across trusted/untrusted contexts.

## Moonshot
Support sandboxed community-authored automation with fine-grained, revocable permissions and locally inspectable guarantees.

## Proving scenario
A malicious character card cannot trick a worker into editing another world or reading provider secrets; harmless content still imports.

## Independence and interfaces
Security policy applies to existing tools now; agents and marketplaces opt into these capabilities later.

## Open questions
- Which existing tool APIs run with excessive authority?
- Which scripting features need actual isolation?
- How should user-visible approval be scoped and revoked?
