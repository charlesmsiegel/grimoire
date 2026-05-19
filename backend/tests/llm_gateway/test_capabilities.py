"""Tests for the static provider-capabilities table."""

from __future__ import annotations

from grimoire.llm_gateway.capabilities import ProviderCapabilities, capabilities_for


def test_anthropic_supports_tool_use():
    caps = capabilities_for("anthropic")
    assert caps.supports_tool_use is True
    assert caps.streaming_tool_use is True
    assert caps.max_tool_count >= 64


def test_openai_supports_tool_use():
    caps = capabilities_for("openai")
    assert caps.supports_tool_use is True
    assert caps.streaming_tool_use is True


def test_google_supports_tool_use_without_streaming():
    caps = capabilities_for("google")
    assert caps.supports_tool_use is True
    assert caps.streaming_tool_use is False


def test_openrouter_safe_default():
    caps = capabilities_for("openrouter")
    assert caps.supports_tool_use is False


def test_unknown_provider_safe_default():
    caps = capabilities_for("brand-new-llm-co")
    assert caps.supports_tool_use is False
    assert isinstance(caps, ProviderCapabilities)
