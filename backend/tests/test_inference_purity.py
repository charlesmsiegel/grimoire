"""``grimoire.core.inference`` is a separate vocabulary from the fiction core.

Its purity (no I/O, clock, randomness or providers) is checked with every other
pure package in ``test_purity.py``.
"""

import grimoire.core
import grimoire.core.inference as inference


def test_inference_is_not_exported_from_fiction_core():
    exported = {id(getattr(grimoire.core, name)) for name in grimoire.core.__all__}
    assert not set(inference.__all__) & set(grimoire.core.__all__)
    assert not {id(getattr(inference, name)) for name in inference.__all__} & exported
