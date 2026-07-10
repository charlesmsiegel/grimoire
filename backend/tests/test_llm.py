from grimoire.llm import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"
