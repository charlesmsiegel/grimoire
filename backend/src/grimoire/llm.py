"""Provider-agnostic LLM surface: shared error type and (Task 4) dispatch facade."""

from __future__ import annotations


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response | missing_dependency
        self.detail = detail or kind
