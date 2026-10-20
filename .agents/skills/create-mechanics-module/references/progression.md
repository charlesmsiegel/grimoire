# Optional creation and advancement

Read this when a sheet type needs point-buy creation or experience spending.
The current contracts are validated by
[validate.py](../../../../backend/src/grimoire/store/modules/validate.py),
`_validate_creation` and `_validate_advancement`.

`creation.pools` maps a composed group id to its budget and per-field costs.
The pool must name a group that the sheet type composes. `budget` is an integer
or a constant expression; it cannot reference sheet fields. `costs` names fields
from that group and gives positive integer costs.

`advancement.pool` names a resource field on the assembled sheet type.
`advancement.costs` maps a number/dots field to a positive integer expression
over the sheet's numeric names plus `new`, the proposed rating. Text, list, ref,
track and resource fields cannot be advancement targets. Validate against the
current module validator before writing starting sheets.
