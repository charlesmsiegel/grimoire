---
name: verify
description: "Use when verifying Grimoire changes end to end in a browser against an isolated scratch library and mocked providers."
---

# Verifying Grimoire end to end

Use a scratch `GRIMOIRE_HOME` set before importing Grimoire and free loopback
ports and a fresh browser profile. The user's backend and frontend may be on
8173 and 5173; never reuse them. Exercise only placeholder data and keep captures in ignored scratch space.

1. Reuse the shared pytest fixtures to isolate bootstrap paths and process locks.
   [Launch isolated servers and block real provider calls](references/launch.md).
   Patch every provider door the tested flow can reach, not just generation.
2. [Seed current settings, drive the browser and stop both servers](references/exercise.md).
3. Report the behavior observed and any untested paths. Use the current browser
   tool available in the host; a particular MCP or process-control tool is not
   required by this skill.

For images committed as documentation, follow the additional
[screenshot procedure](../../../docs/screenshots/README.md).
