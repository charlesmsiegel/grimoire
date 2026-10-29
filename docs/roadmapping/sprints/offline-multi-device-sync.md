# Offline Multi-Device Sync and Conflict-Preserving Reconciliation

**Outcome:** Play and edit the same portable library on desktop and Android without silent loss when devices temporarily diverge.

## Scope and ownership
- Distinguish ordinary backups and one-device-at-a-time sync from **real** multi-device revision tracking and reconciliation.
- Detect concurrent scene, actor, media and world modifications using identity/version evidence; never guess the authoritative copy from timestamps.
- Present reviewable merge, branch or keep-both decisions and preserve external-editor changes; protect privacy and credentials.
- Support offline queues and interrupted transfer with recoverable, testable results without making a remote database mandatory.

## Milestones
1. Produce a dry-run conflict report for two copies of one library edited while disconnected.
2. Reconcile a non-conflicting scene/asset update and explicitly fork a conflicting modification without overwriting either.
3. Support resumable offline-to-online sync across desktop and Android with staged preview and audit.

## Moonshot
A private, local-first multi-device library works seamlessly across phones, tablets and desktops, including offline sessions, branching stories and externally edited content.

## Proving scenario
Two devices edit the same scene independently, reconnect and both changes remain available for explicit resolution. No conflict is silently discarded or falsely reported merged.

## Interfaces and independence
Builds on readable file authority and source revisions. **Not** the same sprint as multiplayer: devices syncing a library asynchronously differ from concurrent networked co-play.

## Open questions
- What revision/operation IDs survive manual copies and external edits?
- How are credentials and adult/private stories excluded from transport?
- What conflict kinds are resolvable without a domain-specific merge?
