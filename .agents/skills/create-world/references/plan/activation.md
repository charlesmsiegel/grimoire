# Activation and secrecy

Source: [activation.py](../../../../../backend/src/grimoire/store/context/activation.py), [world_state.py](../../../../../backend/src/grimoire/store/context/world_state.py).

### 4. Decide activation, per entry

This is where a World most often does the opposite of what its author meant. It is
also the decision the prompt is built from (`context/world_state.py`):

- **Keyed, unowned**: enters the prompt when a key appears, whole-word and
  case-insensitive, in recent scene text, or when semantic recall judges it close
  to the scene (`context/semantic.py`), if that is enabled. This is the right
  default for nearly every entry. Keys are the names people will actually *say*: the full name, the
  short name, an in-world synonym.
- **Keyless, unowned lore/item/group/creature**: **always on**, in every turn's
  prompt, and every turn pays for it. Reserve it for the few facts that are true of
  every scene (the setting's one-paragraph premise, say).
- **Keyless location**: never always-on. A location with no keys reaches the prompt
  **only as the current setting** of a scene set there, never because someone
  mentions it. Give a location keys if it should come up when talked about.
- **Owned** (`owners`): silent unless an owner is in the scene, and then the keyed
  or keyless rule above applies. This is how a character's private history stays
  out of scenes they aren't in.
- **`secrecy: secret`**: activates as normal but renders under a heading telling the
  model that uninvolved characters don't know it. **`gm-only`**: never reaches a
  prompt at all, so it is author's notes.

`check` lists every keyless location and every always-on entry, so you can confirm
each one was meant.
