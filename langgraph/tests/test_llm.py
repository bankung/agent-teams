"""Unit tests for the multi-provider chat-model factory.

No real API keys are required — `make_chat_model()` constructs the langchain
client object but does NOT call the provider; missing-key + bad-provider paths
raise before any network attempt. The "constructs successfully with a fake
key" tests use throwaway strings.

Tests are heavy on `monkeypatch.setenv` / `monkeypatch.delenv` so each case
isolates the env-var matrix from the surrounding process — important since
the langgraph container DOES carry real env-vars in `docker compose exec`.
"""

from __future__ import annotations

import pytest

import llm
from llm import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    make_chat_model,
    resolve_model,
    resolve_provider,
)

# ---------------------------------------------------------------------------
# Fixture — strip every LLM env-var so each test starts from a clean slate.
# Individual tests opt back in with monkeypatch.setenv.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LANGGRAPH_LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_MODEL",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# resolve_provider()
# ---------------------------------------------------------------------------


def test_resolve_provider_default_is_anthropic() -> None:
    assert resolve_provider() == "anthropic"


@pytest.mark.parametrize("value", ["anthropic", "openai", "ANTHROPIC", "OpenAI", "  anthropic  "])
def test_resolve_provider_accepts_normalized_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", value)
    assert resolve_provider() == value.strip().lower()


@pytest.mark.parametrize("value", ["bogus", "claude", "gpt", "", "azure"])
def test_resolve_provider_rejects_unknown(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", value)
    with pytest.raises(RuntimeError) as excinfo:
        resolve_provider()
    msg = str(excinfo.value)
    assert "Unknown LANGGRAPH_LLM_PROVIDER" in msg
    # Error message must point the operator at the fix.
    assert "anthropic" in msg and "openai" in msg


# ---------------------------------------------------------------------------
# resolve_model()
# ---------------------------------------------------------------------------


def test_resolve_model_default_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    assert resolve_model() == DEFAULT_ANTHROPIC_MODEL


def test_resolve_model_default_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    assert resolve_model() == DEFAULT_OPENAI_MODEL


def test_resolve_model_override_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    assert resolve_model() == "claude-opus-4-7"


def test_resolve_model_override_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    assert resolve_model() == "gpt-4o-mini"


def test_resolve_model_explicit_provider_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    # Cross-resolution: provider env says anthropic, but we ask for openai's default.
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    assert resolve_model("openai") == DEFAULT_OPENAI_MODEL


def test_resolve_model_rejects_underscore_typo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Common gotcha: copy-pasting `claude_sonnet_4_6` (snake_case) instead
    of the canonical hyphen form. The regex must reject this loudly so the
    operator finds the typo at startup, not after the SDK call 404s."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude_sonnet_4_6")
    with pytest.raises(RuntimeError) as excinfo:
        resolve_model()
    assert "Invalid model name" in str(excinfo.value)
    assert "underscores" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["Claude-Sonnet-4-6", "gpt 4o", "", "../etc/passwd"])
def test_resolve_model_rejects_bad_shapes(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", bad)
    with pytest.raises(RuntimeError):
        resolve_model()


# ---------------------------------------------------------------------------
# make_chat_model() — happy paths (uses fake keys; no network)
# ---------------------------------------------------------------------------


def test_make_chat_model_anthropic_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-not-real")
    model = make_chat_model()
    # ChatAnthropic exposes the chosen model name on `.model`.
    assert getattr(model, "model", None) == DEFAULT_ANTHROPIC_MODEL
    assert type(model).__name__ == "ChatAnthropic"


def test_make_chat_model_openai_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-not-real")
    model = make_chat_model()
    # ChatOpenAI exposes `.model_name` (and also `.model`); accept either.
    name = getattr(model, "model_name", None) or getattr(model, "model", None)
    assert name == DEFAULT_OPENAI_MODEL
    assert type(model).__name__ == "ChatOpenAI"


def test_make_chat_model_explicit_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    model = make_chat_model(model="claude-haiku-4")
    assert getattr(model, "model", None) == "claude-haiku-4"


# ---------------------------------------------------------------------------
# make_chat_model() — failure paths
# ---------------------------------------------------------------------------


def test_make_chat_model_missing_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    # ANTHROPIC_API_KEY intentionally unset by the autouse fixture.
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    msg = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "unset or empty" in msg
    # Must NOT mention OPENAI_API_KEY — wrong-pointer error misleads ops.
    assert "OPENAI_API_KEY" not in msg


def test_make_chat_model_empty_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace-only key counts as unset — common .env mishap (trailing
    space after `ANTHROPIC_API_KEY=`)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


def test_make_chat_model_missing_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "openai")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    msg = str(excinfo.value)
    assert "OPENAI_API_KEY" in msg
    assert "ANTHROPIC_API_KEY" not in msg


def test_make_chat_model_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "bogus")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    assert "Unknown LANGGRAPH_LLM_PROVIDER" in str(excinfo.value)
    assert "'bogus'" in str(excinfo.value)


def test_make_chat_model_invalid_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model(model="bad model name with spaces")
    assert "Invalid model name" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Module-level constants — exposed for discoverability + #851 README cross-ref.
# ---------------------------------------------------------------------------


def test_default_model_constants_are_canonical() -> None:
    assert DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-4-6"
    assert DEFAULT_OPENAI_MODEL == "gpt-4o"
    # Constants must themselves pass the model-name regex (catches a future
    # typo in this file).
    assert llm._MODEL_NAME_RE.match(DEFAULT_ANTHROPIC_MODEL)
    assert llm._MODEL_NAME_RE.match(DEFAULT_OPENAI_MODEL)
