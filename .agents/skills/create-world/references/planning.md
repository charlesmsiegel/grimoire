# Interview and confirm the roster

### 1. Interview

Ask until you could write the plan's first draft without guessing. A World authored
on a wrong guess means renaming records later, which is the one thing ids make
expensive (step 2). Cover:

- **Premise, genre, tone, era.** What kind of story is played here, and what does a
  typical scene feel like?
- **Who the player is.** One kind of player character, or several (outsider vs.
  insider, human vs. not)? Each kind that changes *which scenes make sense* becomes
  a **tag**. Tags are a player-character vocabulary, not a keyword index.
- **The cast.** Who the player meets first, who recurs, who is behind things. For
  each one: what they want, who they are to the others, and how they talk.
- **The furniture.** Places where things happen, organisations with members,
  possessions that matter, the setting's own concepts (its magic, its politics, its
  history), and creatures if the genre has them.
- **What's secret.** What the narrator may know but uninvolved characters may not,
  and what is for the author's eyes only.
- **Openings.** The first scenes a campaign could start with, and whether any of
  them only make sense after another.
- **Time and mechanics.** Real-world dates or a fictional calendar? A dice system,
  or freeform?

Ask the user about decisions that fork the plan: one PC kind or several,
which calendar, and whether to bind a module. Use the host's question tool
when available, or ask in conversation.

### 2. Sketch the names, and confirm them

List every record by kind and name, with a line each, and get the user's agreement
**before** applying anything. An id is derived from the name at creation and is
**stable for life**: world→campaign sync keys on it (`store/entities.py`). Renaming a
record in the app changes only its `name`. Renaming it in the plan makes a *new*
record, because `apply` finds records by name. Names that are right the first time
save a cleanup later.
