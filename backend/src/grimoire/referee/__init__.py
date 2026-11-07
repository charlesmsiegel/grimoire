"""Narration continuity referee (Sprint 047, first slice): grounded findings about a proposed narration.

Pure: no files, clocks, randomness, providers or model calls. ``referee`` checks labelled claims of one
proposed narration against the records other sprints own (041's stage, 023's holdings, 034's possessions,
012's knowledge, 029's resolutions, 040's obstacle states) and reports each contradiction with the records it
rests on, without changing the narration. ``mislabels`` and ``flag_mislabel`` route authorship claims that
003's correction flow cannot reach. See docs/REFEREE.md.
"""

from .check import referee
from .mislabel import MislabelFlag, flag_mislabel, mislabels
from .model import (
    CATEGORIES, CLASSIFICATIONS, KINDS, MAX_CLAIMS, MAX_COUNT, MAX_TEXT, MODES, SOURCES, Authority, ContinuityClaim,
    Evidence, Facts, Finding, LabelledClaim, Mislabel, RefereeError, RefereeReport,
)

__all__ = [
    "CATEGORIES", "CLASSIFICATIONS", "KINDS", "MAX_CLAIMS", "MAX_COUNT", "MAX_TEXT", "MODES", "SOURCES", "Authority",
    "ContinuityClaim", "Evidence", "Facts", "Finding", "LabelledClaim", "Mislabel", "MislabelFlag", "RefereeError",
    "RefereeReport", "flag_mislabel", "mislabels", "referee",
]
