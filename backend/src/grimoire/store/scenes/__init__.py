"""Scene CRUD — chat transcripts living under <campaign>/scenes/.

Every mutator here runs under `@_serialized` — directly, or (where user-authored
calendar code has to be resolved first) through a decorated inner. A scene file
is rewritten whole, so two concurrent read-modify-writes silently lose one of
them. See that decorator.
"""

from __future__ import annotations

# Submodules before names, and in dependency order: `serialize` reaches into
# `paths`, `read`/`turns` into both, `write` into `read`/`turns`, `moment` into
# `write`, and `lifecycle` into `serialize`. The order is a reading aid, not a
# requirement -- binding a submodule imports it on demand whatever the order,
# so listing them this way just makes the graph readable off the line.
from . import (  # noqa: F401
    identity,
    lifecycle,
    locking,
    moment,
    paths,
    read,
    serialize,
    turns,
    write,
)
from .identity import (  # noqa: F401
    ensure_identity,
    find_by_identity,
    scene_identity,
    scene_identity_strict,
)
from .lifecycle import (  # noqa: F401
    _create_scene,
    _date_hint,
    create_scene,
    delete_scene,
    rename_scene,
    repad,
)
from .locking import _serialized  # noqa: F401
from .moment import _apply_datetime, _stamp_start_date, set_datetime, set_location  # noqa: F401
from .paths import SceneNotFound, _require_campaign, _scene_path, _scenes_dir  # noqa: F401
from .read import (  # noqa: F401
    get_dismissed,
    get_location_history,
    get_rolling_summary,
    get_scene_break,
    get_suggested_date,
    get_time_history,
    histories,
    is_pcless,
    list_scenes,
    read_scene,
    read_scene_meta,
    read_scene_window,
    rolling_summary_fields,
    scene_break_fields,
    trailing_transitions,
)
from .serialize import (  # noqa: F401
    _MARKER,
    _SAFE_LABEL,
    RESERVED_LABELS,
    ROLE_TO_LABEL,
    ROLL_SPEAKER,
    SYNTHETIC_SPEAKERS,
    TRANSITION_SPEAKER,
    _append_block,
    _block,
    _label,
    _markers,
    _numbering,
    _parse_messages,
    _serialize_messages,
    _speaker_and_role,
    confusable,
    label_preserved,
    match_name,
    speaker_base,
)
from .turns import (  # noqa: F401
    TurnSizesDesynced,
    _model_blocks,
    _parse_turn_sizes,
    _reconciled_turn_sizes,
    _set_turn_sizes,
    _tracked_suffix_fits,
    _trailing_model_run,
    get_turn_sizes,
)
from .write import (  # noqa: F401
    RESPONSE_FIELDS,
    RollMessageImmutable,
    add_dismissed,
    append_message,
    append_reply,
    delete_from,
    dismiss_scene_break,
    edit_message,
    mark_absorbed,
    remove_trailing_assistant_run,
    remove_trailing_user_post,
    restore_trailing_assistant_run,
    set_pcless,
    set_response,
    set_rolling_summary,
    set_scene_break,
    split_reply,
    stamp_greeting,
    stamp_user_speaker,
    trim_continuation,
    unmark_absorbed,
)
