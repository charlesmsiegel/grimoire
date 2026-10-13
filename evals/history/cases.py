"""Independent recipe shapes, rather than examples selected by the retriever."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    shape: int
    label: str
    query: str
    terms: tuple[str, ...]

    @property
    def id(self):
        return f"history-{self.shape}"


CASES = (
    Case(1, "old evidence", "silver ledger", ("silver", "ledger")),
    Case(2, "wording distractor", "silver ledger", ("silver", "ledger")),
    Case(3, "structure without words", "Return what was entrusted.", ("return", "entrusted")),
    Case(4, "generic query", "You knew the whole time, didn't you?", ("knew", "whole", "time")),
    Case(5, "short excerpt", "silver ledger", ("silver", "ledger")),
    Case(6, "closed branch", "silver ledger", ("silver", "ledger")),
    Case(7, "future scene", "silver ledger", ("silver", "ledger")),
    Case(8, "exclusions and secrecy", "Mara silver ledger", ("mara", "silver", "ledger")),
    Case(9, "first and young", "silver ledger", ("silver", "ledger")),
    Case(10, "legacy identities", "silver ledger", ("silver", "ledger")),
)
ARMS = ("context-only", "lexical", "semantic", "structural+lexical", "hybrid")
