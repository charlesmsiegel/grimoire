# 07-S7 native implementation review

Native /codex:review at7f21b9a24, thread01a12f62-75ff-7933-981a-3595f0f245a2,
found one P2: a one-actor cast in a large group traversed every off-scene member
through affiliated() before intersecting. This violated spec12.2's inverse cost.

An independent tuple traversal guard reproduced RED. Present sets now accumulate
while visiting the cast's inverse entries; candidate roster reads retrieve only
name/secrecy/leader metadata.8 focused projection cases GREEN; lint1146/mypy161.
Original independent gate non-blocking; corrected native and final gates pending.
