# Calendar and mechanics binding

### 6. Calendar and module

- **Calendar.** `gregorian` and `hebrew` ship in `store/calendars/`, and a fictional
  calendar is a plugin the user keeps in `<GRIMOIRE_HOME>/calendars/`
  (`store/calendars/plugins.py`). Never author one into the repo. A plan may give
  just the primary block (`{"provider": ..., "region": ...}`) or the whole config
  (`{"primary": {...}, "secondary": {...}}`). An authored calendar is saved as
  `confirmed`, so campaigns made from this World start on it without asking.
- **Module.** Name a mechanics module by id (`pool-basic`, `d20-basic`, or one in the
  user library) to make it the World's default; `"module": ""` clears it, and
  omitting the key leaves it as it is. `apply` refuses to *change* the
  module of a World that already has campaigns, because rebinding those campaigns
  needs the world editor's locking. Change it there instead.
