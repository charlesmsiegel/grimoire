"""Stable scene meaning, excluding effects and inference/action bookkeeping."""
import hashlib
import json
from collections.abc import Mapping

# Keep all authored line/source/audience fields. Calls, labels, intentions and effect receipts
# advance independently during acceptance and cannot invalidate the source they just accepted.
TURN_FIELDS = ('chat', 'number', 'origin', 'post', 'paused', 'paused_by', 'passed_by', 'device', 'follows', 'set_aside')


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def scene_revision(payload: Mapping) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError('scene payload must be a mapping')
    projected = {key: payload[key] for key in TURN_FIELDS if key in payload}
    projected['takes'] = [{key: value for key, value in take.items() if key != 'calls'}
                          for take in payload.get('takes', ())]
    data = json.dumps(_plain(projected), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return 'sha256:' + hashlib.sha256(data.encode('utf-8')).hexdigest()
