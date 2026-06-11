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
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_NUM_CTX,
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
        # Kanban #891 — Ollama env-vars also scrubbed so tests don't inherit
        # whatever the docker-compose langgraph env block injects.
        "OLLAMA_MODEL",
        "OLLAMA_BASE_URL",
        "LANGGRAPH_OLLAMA_NUM_CTX",  # Kanban #2120
        # Kanban #1951 — Google / Gemini env-vars scrubbed for the same reason.
        "GOOGLE_API_KEY",
        "GOOGLE_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# resolve_provider()
# ---------------------------------------------------------------------------


def test_resolve_provider_default_is_anthropic() -> None:
    assert resolve_provider() == "anthropic"


@pytest.mark.parametrize(
    "value",
    [
        "anthropic",
        "openai",
        "ANTHROPIC",
        "OpenAI",
        "  anthropic  ",
        # Kanban #891 — ollama provider accepted with same normalization rules.
        "ollama",
        "OLLAMA",
        "  Ollama  ",
        # Kanban #1951 — google provider accepted with same normalization rules.
        "google",
        "Google",
        "  google  ",
    ],
)
def test_resolve_provider_accepts_normalized_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", value)
    assert resolve_provider() == value.strip().lower()


@pytest.mark.parametrize("value", ["bogus", "claude", "gpt", "", "azure", "deepseek"])
def test_resolve_provider_rejects_unknown(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", value)
    with pytest.raises(RuntimeError) as excinfo:
        resolve_provider()
    msg = str(excinfo.value)
    assert "Unknown LANGGRAPH_LLM_PROVIDER" in msg
    # Error message must point the operator at the fix — all valid values must
    # appear so the operator sees the full menu when picking a fix.
    # Kanban #891 added ollama; #1951 added google; deepseek removed #1838.
    assert "anthropic" in msg
    assert "openai" in msg
    assert "ollama" in msg
    assert "google" in msg
    # The error message mentions the SET of valid providers; deepseek must not
    # be listed there (the raw input value may still appear in the echo).
    assert "deepseek" not in msg.split("expected one of")[1]


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
    # Kanban #891 — ollama defaults pinned here to catch a future drift.
    assert DEFAULT_OLLAMA_MODEL == "llama3.2"
    assert DEFAULT_OLLAMA_BASE_URL == "http://host.docker.internal:11434"
    # Kanban #1951 — google/gemini default pinned here to catch a future drift.
    assert DEFAULT_GOOGLE_MODEL == "gemini-flash-latest"
    # Constants must themselves pass the model-name regex (catches a future
    # typo in this file).
    assert llm._MODEL_NAME_RE.match(DEFAULT_ANTHROPIC_MODEL)
    assert llm._MODEL_NAME_RE.match(DEFAULT_OPENAI_MODEL)
    assert llm._OLLAMA_MODEL_NAME_RE.match(DEFAULT_OLLAMA_MODEL)
    assert llm._MODEL_NAME_RE.match(DEFAULT_GOOGLE_MODEL)


# ---------------------------------------------------------------------------
# Ollama provider (Kanban #891) — free local LLM via http://host.docker.internal:11434.
# Construction-only: ChatOllama does NOT call the server in __init__, so these
# tests pass without an Ollama server running.
# ---------------------------------------------------------------------------


def test_resolve_model_default_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    assert resolve_model() == DEFAULT_OLLAMA_MODEL


def test_resolve_model_override_ollama_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama tag overrides via OLLAMA_MODEL; tag may include `:` for size/quant."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    assert resolve_model() == "qwen2.5:7b"


@pytest.mark.parametrize(
    "tag",
    [
        "llama3.2",
        "qwen2.5:7b",
        "llama3.2:3b-instruct-q4_K_M",
        "mistral:7b-instruct",
        # Kanban #891 — minimal tag (no colon, no dot) still valid.
        "phi3",
    ],
)
def test_resolve_model_accepts_ollama_tag_shapes(
    monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", tag)
    assert resolve_model() == tag


def test_anthropic_underscore_typo_still_rejected_after_ollama_widening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the per-provider regex split (Kanban #891).

    Widening the regex to accept `_`/`:` for ollama MUST NOT loosen the
    anthropic check — `claude_sonnet_4_6` (underscore-typo) is the canonical
    gotcha; if this passes, the per-provider split has collapsed into a
    single permissive regex by accident.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude_sonnet_4_6")
    with pytest.raises(RuntimeError) as excinfo:
        resolve_model()
    assert "Invalid model name" in str(excinfo.value)


def test_make_chat_model_ollama_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default Ollama path: provider=ollama, no other env-vars. Returns
    ChatOllama with the default model + base_url.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    model = make_chat_model()
    assert type(model).__name__ == "ChatOllama"
    assert getattr(model, "model", None) == DEFAULT_OLLAMA_MODEL
    assert getattr(model, "base_url", None) == DEFAULT_OLLAMA_BASE_URL


def test_make_chat_model_ollama_honors_base_url_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OLLAMA_BASE_URL override (e.g., running Ollama on the same host as the
    langgraph container in non-Docker dev, or on a remote box)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = make_chat_model()
    assert type(model).__name__ == "ChatOllama"
    assert getattr(model, "base_url", None) == "http://localhost:11434"


def test_make_chat_model_ollama_honors_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
    model = make_chat_model()
    assert getattr(model, "model", None) == "qwen2.5:7b"


def test_make_chat_model_ollama_requires_no_api_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of ollama: no paid keys needed. With provider=ollama
    and ALL three API key env-vars unset, make_chat_model() must NOT raise.

    Regression guard for the obvious bug — accidentally calling
    _require_api_key("ollama") would fail unhelpfully ("ANTHROPIC_API_KEY
    unset") on an ollama deployment.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    # delenv on all three to be explicit; the autouse fixture already deletes
    # ANTHROPIC_API_KEY + OPENAI_API_KEY, but pin it here for the regression
    # test to be self-contained.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    model = make_chat_model()
    assert type(model).__name__ == "ChatOllama"


def test_make_chat_model_ollama_empty_base_url_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only OLLAMA_BASE_URL counts as unset — common .env mishap.
    Matches the empty-API-key handling for anthropic/openai.
    """
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "   ")
    model = make_chat_model()
    assert getattr(model, "base_url", None) == DEFAULT_OLLAMA_BASE_URL


def test_make_chat_model_ollama_explicit_model_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller passes model= explicitly, bypassing OLLAMA_MODEL env-var."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    model = make_chat_model(model="qwen2.5:7b")
    # Explicit arg wins over env.
    assert getattr(model, "model", None) == "qwen2.5:7b"


def test_make_chat_model_ollama_invalid_model_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even the widened ollama regex rejects spaces / uppercase."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "Llama 3.2")  # space + uppercase
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    assert "Invalid model name" in str(excinfo.value)


# Kanban #2120 — num_ctx tests


def test_make_chat_model_ollama_default_num_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGGRAPH_OLLAMA_NUM_CTX unset → ChatOllama receives DEFAULT_OLLAMA_NUM_CTX."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    model = make_chat_model()
    assert getattr(model, "num_ctx", None) == DEFAULT_OLLAMA_NUM_CTX


def test_make_chat_model_ollama_num_ctx_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LANGGRAPH_OLLAMA_NUM_CTX env-var is honored."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LANGGRAPH_OLLAMA_NUM_CTX", "8192")
    model = make_chat_model()
    assert getattr(model, "num_ctx", None) == 8192


def test_make_chat_model_ollama_invalid_num_ctx_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-integer and non-positive LANGGRAPH_OLLAMA_NUM_CTX must raise RuntimeError."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LANGGRAPH_OLLAMA_NUM_CTX", "notanumber")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    assert "LANGGRAPH_OLLAMA_NUM_CTX" in str(excinfo.value)

    monkeypatch.setenv("LANGGRAPH_OLLAMA_NUM_CTX", "0")
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    assert "LANGGRAPH_OLLAMA_NUM_CTX" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Google / Gemini native provider (Kanban #1951) — ChatGoogleGenerativeAI.
# Construction-only: ChatGoogleGenerativeAI does NOT call the API in __init__,
# so these tests pass without a real GOOGLE_API_KEY (as long as the SDK is
# installed in the container where they run).
# ---------------------------------------------------------------------------


def test_resolve_provider_accepts_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    assert resolve_provider() == "google"


def test_resolve_model_default_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    assert resolve_model() == DEFAULT_GOOGLE_MODEL


def test_resolve_model_override_google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOOGLE_MODEL env-var override is honored."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.5-flash")
    assert resolve_model() == "gemini-2.5-flash"


def test_require_api_key_reads_google_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """_require_api_key reads GOOGLE_API_KEY for the google provider."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    # Key unset → RuntimeError naming GOOGLE_API_KEY (not OPENAI/ANTHROPIC).
    with pytest.raises(RuntimeError) as excinfo:
        make_chat_model()
    msg = str(excinfo.value)
    assert "GOOGLE_API_KEY" in msg
    assert "unset or empty" in msg
    assert "ANTHROPIC_API_KEY" not in msg
    assert "OPENAI_API_KEY" not in msg


def test_make_chat_model_google_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default Google path: returns ChatGoogleGenerativeAI with gemini-flash-latest."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSy-fake-not-real")
    model = make_chat_model()
    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    # ChatGoogleGenerativeAI exposes the model name on `.model`.
    assert getattr(model, "model", None) == DEFAULT_GOOGLE_MODEL


def test_make_chat_model_google_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caller passes model= explicitly; GOOGLE_MODEL env is ignored."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSy-fake-not-real")
    monkeypatch.setenv("GOOGLE_MODEL", "gemini-2.5-flash")
    model = make_chat_model(model="gemini-flash-latest")
    assert getattr(model, "model", None) == "gemini-flash-latest"


# ---------------------------------------------------------------------------
# make_chat_model(effort=...) — Anthropic effort lever (Kanban #2300)
#
# Asserts on the ACTUAL kwarg path wired in llm.py: the top-level `effort`
# ctor param (langchain_anthropic's convenience shorthand → output_config.effort)
# + native `thinking={"type":"adaptive"}`. Verified vs langchain_anthropic==1.4.3.
# ---------------------------------------------------------------------------


def _anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-not-real")


@pytest.mark.parametrize(
    "effort,expected_effort",
    [("low", "low"), ("medium", "medium"), ("high", "high"), ("extra", "xhigh"),
     ("max", "max")],
)
def test_anthropic_effort_enables_adaptive_thinking(
    monkeypatch: pytest.MonkeyPatch, effort, expected_effort
) -> None:
    """effort low/medium/high/extra/max → adaptive thinking + mapped effort.

    'extra' maps to the API's 'xhigh' tier; the rest pass through. (max is a
    legal carrier value reachable only manually — make_chat_model still honors it.)
    """
    _anthropic_env(monkeypatch)
    model = make_chat_model(effort=effort)
    assert model.thinking == {"type": "adaptive"}, model.thinking
    assert model.effort == expected_effort, model.effort


def test_anthropic_extra_maps_to_xhigh_in_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native `effort` shorthand lands as output_config.effort='xhigh' AND
    thinking={type:adaptive} in the Messages-API payload (design lock D1)."""
    _anthropic_env(monkeypatch)
    model = make_chat_model(effort="extra")
    payload = model._get_request_payload([], stop=None)
    assert payload.get("output_config") == {"effort": "xhigh"}, payload.get("output_config")
    assert payload.get("thinking") == {"type": "adaptive"}, payload.get("thinking")


@pytest.mark.parametrize("effort", [None, "off"])
def test_anthropic_effort_none_or_off_is_noop(
    monkeypatch: pytest.MonkeyPatch, effort
) -> None:
    """effort None / 'off' → EXACTLY today's construction (no thinking, no effort).

    Bit-identical no-op: the kwargs MUST match the zero-arg construction so the
    live default path is unchanged.
    """
    _anthropic_env(monkeypatch)
    baseline = make_chat_model()
    model = make_chat_model(effort=effort)
    # POSITIVE: an effort run DOES set these (proven in the test above).
    # NEGATIVE/lock: None/off leaves thinking + effort unset, same as baseline.
    assert model.thinking is None, model.thinking
    assert model.effort is None, model.effort
    assert model.thinking == baseline.thinking
    assert model.effort == baseline.effort


@pytest.mark.parametrize("provider", ["ollama", "openai", "google"])
def test_effort_ignored_on_non_anthropic(
    monkeypatch: pytest.MonkeyPatch, provider
) -> None:
    """openai/google/ollama ignore `effort` — same construction with/without it
    (the lever is Anthropic-only, design lock D7)."""
    monkeypatch.setenv("LANGGRAPH_LLM_PROVIDER", provider)
    if provider == "openai":
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    elif provider == "google":
        monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSy-fake")
    a = make_chat_model()
    b = make_chat_model(effort="high")
    assert type(a).__name__ == type(b).__name__
    assert getattr(a, "model", None) == getattr(b, "model", None)
    # No anthropic-only thinking/effort attrs leak onto these clients.
    assert getattr(a, "thinking", None) == getattr(b, "thinking", None)
