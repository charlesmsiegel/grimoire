"""The error type every LLM provider raises.

A leaf module on purpose: the providers need it and `llm.py` needs the
providers, so parking it in `llm.py` made the two import each other (#239).
Nothing here may import from the rest of the package.
"""

from __future__ import annotations


class LLMError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response | missing_dependency
        self.detail = detail or kind
