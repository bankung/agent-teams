"""Static registry of OPTIONAL platform integrations (Kanban #1655).

The "Integrations" settings popup is driven entirely by this module. Each entry
describes one optional integration group: which env var(s) it needs, whether
each is required, and the operator-facing setup guidance (steps + links).

DESIGN — secrets never live here, and never live in the DB:
  - This module is STATIC data only (var names + human guidance). It holds NO
    secret VALUES.
  - `configured` (are the required env vars present?) is computed LIVE from
    os.environ by `is_configured()` / `env_var_presence()` at request time — it
    is NEVER stored. The router returns presence BOOLEANS, never the values.
  - The DB (`platform_integration_settings`) stores ONLY the operator's
    enable/disable toggle, keyed by the `id` field below.

CORE keys (DATABASE_URL, REPO_ROOT, CREDENTIALS_MASTER_KEY, LANGGRAPH_PROJECT_ID)
are platform infrastructure — the platform cannot run without them — so they are
DELIBERATELY ABSENT from this registry. The popup only surfaces OPTIONAL
integrations the operator may turn on or off.

Special case `llm_ollama`: a local Ollama server needs no API key, so it has no
required env vars and `is_configured()` returns True unconditionally.

Links: where a provider's deep URL is volatile, we keep the stable root domain
plus a setup STEP describing the in-product navigation. We do not fabricate deep
paths that may rot.
"""

from __future__ import annotations

import os
from typing import Final, TypedDict


class EnvVarSpec(TypedDict):
    name: str
    required: bool


class SetupLink(TypedDict):
    label: str
    url: str


class IntegrationSetup(TypedDict):
    steps: list[str]
    links: list[SetupLink]


class IntegrationEntry(TypedDict):
    id: str
    label: str
    category: str
    env_vars: list[EnvVarSpec]
    setup: IntegrationSetup


# Integration ids that are configured WITHOUT any env var (local-only services).
# `is_configured()` short-circuits to True for these regardless of env state.
_ALWAYS_CONFIGURED_IDS: Final[frozenset[str]] = frozenset({"llm_ollama"})


# ---------------------------------------------------------------------------
# The registry. One entry per OPTIONAL integration group. Ordered by category
# then rough setup difficulty so the FE can render groups top-to-bottom.
# ---------------------------------------------------------------------------

INTEGRATIONS_REGISTRY: Final[tuple[IntegrationEntry, ...]] = (
    # --- LLM providers -----------------------------------------------------
    {
        "id": "llm_anthropic",
        "label": "Anthropic (Claude)",
        "category": "llm",
        "env_vars": [
            {"name": "ANTHROPIC_API_KEY", "required": True},
        ],
        "setup": {
            "steps": [
                "Sign in at console.anthropic.com.",
                "Open API Keys and create a new key.",
                "Add ANTHROPIC_API_KEY=<your key> to .env and restart the api container.",
            ],
            "links": [
                {"label": "Anthropic Console", "url": "https://console.anthropic.com"},
            ],
        },
    },
    {
        "id": "llm_openai",
        "label": "OpenAI",
        "category": "llm",
        "env_vars": [
            {"name": "OPENAI_API_KEY", "required": True},
        ],
        "setup": {
            "steps": [
                "Sign in at platform.openai.com.",
                "Open API keys and create a new secret key.",
                "Add OPENAI_API_KEY=<your key> to .env and restart the api container.",
            ],
            "links": [
                {"label": "OpenAI API keys", "url": "https://platform.openai.com/api-keys"},
            ],
        },
    },
    {
        "id": "llm_ollama",
        "label": "Ollama (local)",
        "category": "llm",
        # No env var required — a local Ollama server needs no API key.
        # is_configured() returns True unconditionally for this id.
        "env_vars": [],
        "setup": {
            "steps": [
                "Install Ollama from ollama.com.",
                "Pull a model, e.g. `ollama pull llama3`.",
                "Ollama runs locally on 127.0.0.1:11434 — no API key needed.",
            ],
            "links": [
                {"label": "Ollama", "url": "https://ollama.com"},
            ],
        },
    },
    # --- Notifications -----------------------------------------------------
    {
        "id": "web_push",
        "label": "Web Push (VAPID)",
        "category": "notifications",
        "env_vars": [
            {"name": "VAPID_PUBLIC_KEY", "required": True},
            {"name": "VAPID_PRIVATE_KEY", "required": True},
            {"name": "VAPID_SUBJECT", "required": True},
        ],
        "setup": {
            "steps": [
                "Generate a VAPID keypair: run `python api/scripts/generate_vapid_keys.py`.",
                "Copy the printed VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, and "
                "VAPID_SUBJECT (mailto:you@example.com) into .env.",
                "Restart the api container so the keys load.",
            ],
            "links": [],
        },
    },
    {
        "id": "telegram",
        "label": "Telegram Bot",
        "category": "notifications",
        "env_vars": [
            {"name": "TELEGRAM_BOT_TOKEN", "required": True},
        ],
        "setup": {
            "steps": [
                "In Telegram, message @BotFather and send /newbot.",
                "Follow the prompts to name your bot; BotFather replies with a token.",
                "Add TELEGRAM_BOT_TOKEN=<token> to .env and restart the api container.",
            ],
            "links": [
                {"label": "Telegram", "url": "https://telegram.org"},
            ],
        },
    },
    {
        "id": "ntfy",
        "label": "ntfy Push",
        "category": "notifications",
        "env_vars": [
            {"name": "NTFY_TOPIC", "required": True},
            {"name": "PUSH_ENABLED", "required": False},
            {"name": "NTFY_ACCESS_TOKEN", "required": False},
        ],
        "setup": {
            "steps": [
                "Choose a hard-to-guess topic name (anyone who knows it can read "
                "your notifications on the public ntfy.sh server).",
                "Add NTFY_TOPIC=<your topic> to .env; set PUSH_ENABLED=true to "
                "enable sends.",
                "Optional: set NTFY_ACCESS_TOKEN for a private/self-hosted relay.",
            ],
            "links": [
                {"label": "ntfy.sh", "url": "https://ntfy.sh"},
            ],
        },
    },
    {
        "id": "email_digest",
        "label": "Email Digest (Gmail SMTP)",
        "category": "notifications",
        "env_vars": [
            {"name": "GMAIL_SMTP_USER", "required": True},
            {"name": "GMAIL_SMTP_APP_PASSWORD", "required": True},
            {"name": "DIGEST_EMAIL_RECIPIENT", "required": True},
            {"name": "DIGEST_EMAIL_ENABLED", "required": False},
        ],
        "setup": {
            "steps": [
                "Enable 2-Step Verification on your Google Account.",
                "Go to Google Account -> Security -> App passwords and create one "
                "for 'Mail'.",
                "Add GMAIL_SMTP_USER (your address), GMAIL_SMTP_APP_PASSWORD (the "
                "16-char app password), and DIGEST_EMAIL_RECIPIENT to .env; set "
                "DIGEST_EMAIL_ENABLED=true to enable sends.",
            ],
            "links": [
                {
                    "label": "Google App passwords",
                    "url": "https://myaccount.google.com/apppasswords",
                },
            ],
        },
    },
    # --- Email tools (OAuth) ----------------------------------------------
    {
        "id": "gmail_oauth",
        "label": "Gmail (OAuth)",
        "category": "email",
        "env_vars": [
            {"name": "GOOGLE_OAUTH_CLIENT_ID", "required": True},
            {"name": "GOOGLE_OAUTH_CLIENT_SECRET", "required": True},
            {"name": "GOOGLE_OAUTH_REDIRECT_URI", "required": True},
        ],
        "setup": {
            "steps": [
                "Open Google Cloud Console and create (or select) a project.",
                "Go to APIs & Services -> Credentials and create an OAuth 2.0 "
                "Client ID; add your redirect URI.",
                "Add GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and "
                "GOOGLE_OAUTH_REDIRECT_URI to .env and restart the api container.",
            ],
            "links": [
                {
                    "label": "Google Cloud Console — Credentials",
                    "url": "https://console.cloud.google.com/apis/credentials",
                },
            ],
        },
    },
    {
        "id": "outlook_oauth",
        "label": "Outlook (OAuth)",
        "category": "email",
        "env_vars": [
            {"name": "AZURE_OAUTH_CLIENT_ID", "required": True},
            {"name": "AZURE_OAUTH_CLIENT_SECRET", "required": True},
            {"name": "AZURE_OAUTH_REDIRECT_URI", "required": True},
            {"name": "AZURE_OAUTH_TENANT", "required": False},
        ],
        "setup": {
            "steps": [
                "Open the Azure Portal and go to App registrations -> New "
                "registration.",
                "Add a redirect URI and create a client secret under Certificates "
                "& secrets.",
                "Add AZURE_OAUTH_CLIENT_ID, AZURE_OAUTH_CLIENT_SECRET, and "
                "AZURE_OAUTH_REDIRECT_URI to .env (optionally AZURE_OAUTH_TENANT) "
                "and restart the api container.",
            ],
            "links": [
                {
                    "label": "Azure Portal — App registrations",
                    "url": "https://portal.azure.com",
                },
            ],
        },
    },
    # --- Backup ------------------------------------------------------------
    {
        "id": "backup_s3",
        "label": "Off-site Backup (S3 + age)",
        "category": "backup",
        "env_vars": [
            {"name": "BACKUP_S3_BUCKET", "required": True},
            {"name": "BACKUP_S3_ACCESS_KEY_ID", "required": True},
            {"name": "BACKUP_S3_SECRET_ACCESS_KEY", "required": True},
            {"name": "BACKUP_AGE_PUBKEY", "required": True},
        ],
        "setup": {
            "steps": [
                "Create an S3-compatible bucket (AWS S3, Backblaze B2, Cloudflare "
                "R2, or Wasabi) and an access key pair.",
                "Generate an age keypair with `age-keygen`; keep the PRIVATE key "
                "offline.",
                "Add BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY_ID, "
                "BACKUP_S3_SECRET_ACCESS_KEY, and BACKUP_AGE_PUBKEY (the age "
                "public key) to .env and restart the api container.",
            ],
            "links": [
                {"label": "age encryption", "url": "https://github.com/FiloSottile/age"},
            ],
        },
    },
    # --- Feature flags -----------------------------------------------------
    {
        "id": "finance_panels",
        "label": "Finance Panels (UI)",
        "category": "features",
        "env_vars": [
            {"name": "NEXT_PUBLIC_FINANCE_PANELS_ENABLED", "required": True},
        ],
        "setup": {
            "steps": [
                "Set NEXT_PUBLIC_FINANCE_PANELS_ENABLED=true in .env (this is a "
                "frontend build-time flag — no secret).",
                "Rebuild/restart the web container so Next.js picks up the flag.",
            ],
            "links": [],
        },
    },
)


# Fast lookup by id — built once at import.
_REGISTRY_BY_ID: Final[dict[str, IntegrationEntry]] = {
    entry["id"]: entry for entry in INTEGRATIONS_REGISTRY
}


def get_integration(integration_id: str) -> IntegrationEntry | None:
    """Return the registry entry for `integration_id`, or None if unknown."""
    return _REGISTRY_BY_ID.get(integration_id)


def is_known(integration_id: str) -> bool:
    """True iff `integration_id` is a registered integration."""
    return integration_id in _REGISTRY_BY_ID


def _env_present(name: str) -> bool:
    """True iff the env var is set AND non-empty after stripping whitespace.

    Presence is read LIVE from os.environ at call time (matches the
    notify_* / backup env-read pattern) so the popup reflects the current
    process environment without any caching.
    """
    return bool(os.environ.get(name, "").strip())


def env_var_presence(entry: IntegrationEntry) -> list[dict[str, object]]:
    """Return [{name, required, present}] for every env var of `entry`.

    `present` is a BOOLEAN — the env VALUE is never read out into the response.
    """
    return [
        {
            "name": spec["name"],
            "required": spec["required"],
            "present": _env_present(spec["name"]),
        }
        for spec in entry["env_vars"]
    ]


def is_configured(entry: IntegrationEntry) -> bool:
    """True iff every REQUIRED env var of `entry` is present and non-empty.

    `llm_ollama` (and any other id in _ALWAYS_CONFIGURED_IDS) returns True
    unconditionally — a local service needs no key. An entry with no required
    env vars is also configured (the same outcome via the all([]) == True
    short-circuit, kept explicit for clarity).
    """
    if entry["id"] in _ALWAYS_CONFIGURED_IDS:
        return True
    return all(
        _env_present(spec["name"]) for spec in entry["env_vars"] if spec["required"]
    )
