"""051's fixed, actor-local presentation cues. No rationale or another actor's private state."""
from enum import StrEnum


class ContributionMode(StrEnum):
    ANSWER = 'answer'
    INTERJECT = 'interject'
    ASIDE = 'aside'

    @property
    def instruction(self) -> str:
        return {
            self.ANSWER: 'Give a direct answer to the question you heard, in your own voice.',
            self.INTERJECT: 'Make a brief interjection grounded in what you heard or your own intention; do not speak for anyone else.',
            self.ASIDE: 'Make a short side remark to your intended listener, in your own voice.',
        }[self]
