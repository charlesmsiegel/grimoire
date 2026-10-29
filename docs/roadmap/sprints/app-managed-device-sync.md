# Built-in device-to-device sync transport

**Outcome:** Synchronization is a Grimoire capability, not a Syncthing requirement; use authenticated peer or remote transfer over the same revision protocol.

## Scope and first proof
Demonstrate paired sync of a scene, character and picture between two processes over an authenticated connection with conflict/incomplete status.

## Subsequent milestones and moonshot
Add LAN peer discovery, an optional user-chosen reachable remote endpoint or relay, background auto-sync and encrypted transport.

## Independence and interfaces
Owns transfer, pairing and sync status; depends on sync revision core and permissions, not full multiplayer.

## Proving scenario
Offline changes transfer safely once devices connect. When neither device is reachable, sync needs a relay/server or user-provided remote endpoint.

## Open questions
- What user-visible review, failure and rollback flows must the implementation spec prove?
- Which optional later integrations can stay outside the first slice?
