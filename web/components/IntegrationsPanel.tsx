"use client";

// IntegrationsPanel — Kanban #2375 (R5 /settings consolidation).
//
// Read-only "Integrations" status list, extracted verbatim from the body of
// the former platform settings modal (#1655 / #1781) so it can render as a normal
// page panel on /settings — NO ModalShell, NO trigger button, NO modal chrome.
//
// READ-ONLY: no toggle, no PATCH. Status badge is driven by `configured` (all
// required env_vars present). On-demand (?) button per row toggles an inline
// expander showing setup guidance — steps, env var names (+ presence dot +
// required/optional tag), and doc links — so the operator can see how to obtain
// + set each key without cluttering the list.
//
// Keys live in .env; this UI shows STATUS + GUIDANCE only. The contract
// (api.ts getIntegrations) returns env-var PRESENCE (`present: bool`), never a
// value, so this component never touches a secret. All data-* attrs (esp.
// data-integration-status) are preserved from the modal version.

import { useCallback, useEffect, useId, useMemo, useState } from "react";

import {
  getIntegrations,
  type Integration,
  type PlatformSecurity,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";

// ---------------------------------------------------------------------------
// StatusBadge — Configured (emerald/Ready) vs Not configured (amber).
// ---------------------------------------------------------------------------
function StatusBadge({ configured }: { configured: boolean }) {
  return configured ? (
    <span
      data-integration-status="configured"
      className="inline-flex items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500 dark:bg-emerald-400" />
      Ready
    </span>
  ) : (
    <span
      data-integration-status="not-configured"
      className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500 dark:bg-amber-400" />
      Not configured
    </span>
  );
}

// ---------------------------------------------------------------------------
// SetupExpander — inline help panel, toggled by the (?) button on each row.
// Shows ordered steps, env var names (presence dot + required/optional tag),
// and doc/dashboard links. Never renders any value — presence-only.
// ---------------------------------------------------------------------------
function SetupExpander({
  integration,
  panelId,
}: {
  integration: Integration;
  panelId: string;
}) {
  const { steps, links } = integration.setup;
  return (
    <div
      id={panelId}
      role="region"
      aria-label={`Setup guidance for ${integration.label}`}
      data-integration-setup={integration.id}
      className="mt-2 flex flex-col gap-3 rounded border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-800/60"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-400">
        Setup guidance
      </p>

      {steps.length > 0 && (
        <ol className="ml-4 list-decimal space-y-1 text-xs text-zinc-700 dark:text-zinc-300">
          {steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      )}

      {integration.env_vars.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-[11px] font-medium text-zinc-600 dark:text-zinc-400">
            Add these to <code className="font-mono text-zinc-800 dark:text-zinc-200">.env</code> and restart:
          </p>
          <ul className="flex flex-col gap-1">
            {integration.env_vars.map((ev) => (
              <li
                key={ev.name}
                data-integration-env-var={ev.name}
                className="flex items-center gap-2 text-xs"
              >
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    ev.present
                      ? "bg-emerald-500 dark:bg-emerald-400"
                      : "bg-zinc-300 dark:bg-zinc-600"
                  }`}
                  title={ev.present ? "Present" : "Missing"}
                />
                <code className="font-mono text-zinc-900 dark:text-zinc-100">{ev.name}</code>
                {ev.required ? (
                  <span className="rounded bg-red-100 px-1 text-[9px] font-semibold uppercase tracking-wide text-red-700 dark:bg-red-950/40 dark:text-red-300">
                    required
                  </span>
                ) : (
                  <span className="rounded bg-zinc-100 px-1 text-[9px] font-semibold uppercase tracking-wide text-zinc-500 dark:bg-zinc-700 dark:text-zinc-400">
                    optional
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {links.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {links.map((link) =>
            link.url.startsWith("https://") || link.url.startsWith("http://") ? (
              <a
                key={link.url}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                data-integration-link={link.url}
                className="inline-flex items-center gap-1 rounded border border-zinc-300 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
              >
                {link.label}
                <span aria-hidden>↗</span>
              </a>
            ) : (
              <span
                key={link.url}
                className="inline-flex items-center gap-1 rounded border border-zinc-300 bg-white px-2 py-1 text-[11px] font-medium text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
              >
                {link.label}
              </span>
            )
          )}
        </div>
      )}

      <p className="text-[11px] italic text-zinc-500 dark:text-zinc-400">
        Restart required after editing <code className="font-mono not-italic">.env</code>.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IntegrationCard — one integration row: label + status badge + (?) button.
// The (?) button toggles the SetupExpander inline below the row header.
// ---------------------------------------------------------------------------
function IntegrationCard({ integration }: { integration: Integration }) {
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();

  return (
    <li
      data-integration-card={integration.id}
      className="rounded border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
            {integration.label}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge configured={integration.configured} />
          </div>
        </div>

        {/* (?) help button — toggles inline setup expander */}
        <button
          type="button"
          aria-label={`${expanded ? "Hide" : "Show"} setup guidance for ${integration.label}`}
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setExpanded((v) => !v)}
          data-integration-help={integration.id}
          className="inline-flex shrink-0 items-center justify-center rounded-full border border-zinc-300 bg-white text-[11px] font-semibold text-zinc-500 hover:border-zinc-400 hover:text-zinc-700 min-h-[44px] min-w-[44px] sm:min-h-[22px] sm:min-w-[22px] sm:h-[22px] sm:w-[22px] dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-600 dark:hover:text-zinc-200"
        >
          ?
        </button>
      </div>

      {expanded && (
        <SetupExpander integration={integration} panelId={panelId} />
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// IntegrationsPanel — page panel form (no modal). Fetches on mount.
// ---------------------------------------------------------------------------
type LoadState = "idle" | "loading" | "ready" | "error";

export function IntegrationsPanel() {
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [platformSecurity, setPlatformSecurity] = useState<PlatformSecurity | null>(null);

  const load = useCallback(async () => {
    setLoadState("loading");
    setLoadError(null);
    try {
      const { integrations: rows, platform_security } = await getIntegrations();
      setIntegrations(rows);
      setPlatformSecurity(platform_security);
      setLoadState("ready");
    } catch (err: unknown) {
      setLoadError(extractErrorMessage(err, "Failed to load integrations"));
      setLoadState("error");
    }
  }, []);

  // Fetch on mount.
  useEffect(() => {
    void load();
  }, [load]);

  // Group integrations by category, preserving first-seen category order so
  // the BE controls section ordering. Within a category, list order is
  // preserved as received.
  const grouped = useMemo(() => {
    const order: string[] = [];
    const byCategory = new Map<string, Integration[]>();
    for (const it of integrations) {
      if (!byCategory.has(it.category)) {
        byCategory.set(it.category, []);
        order.push(it.category);
      }
      byCategory.get(it.category)!.push(it);
    }
    return order.map((category) => ({
      category,
      items: byCategory.get(category)!,
    }));
  }, [integrations]);

  return (
    <section
      data-integrations-panel
      aria-labelledby="integrations-panel-heading"
      className="flex flex-col gap-4"
    >
      <header className="flex flex-col gap-1">
        <h2
          id="integrations-panel-heading"
          className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
        >
          Integrations
        </h2>
        <p className="text-[12px] text-zinc-500 dark:text-zinc-400 leading-5">
          Platform integration status. Keys live in{" "}
          <code className="font-mono">.env</code> — this panel shows status only.
          Click <strong className="font-semibold">?</strong> on any row to see setup guidance.
        </p>
      </header>

      <div className="flex flex-col gap-4">
        {/* Platform security — Kanban #1658. Always shown (not a toggle).
            Placed at the top so the operator sees vault status before any
            optional integration. Never renders a key value — only a presence
            boolean from the API. */}
        {loadState === "ready" && platformSecurity !== null && (
          <section data-platform-security className="flex flex-col gap-2">
            <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-zinc-500">
              Platform security
            </h3>
            <div className="rounded border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    Vault encryption key
                  </span>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">
                    Encrypts all stored project credentials at rest.
                  </p>
                  <p className="text-[11px] italic text-zinc-400 dark:text-zinc-500">
                    Losing this key makes stored credentials unrecoverable — it lives in <code className="font-mono not-italic">.env</code>.
                  </p>
                </div>
                {platformSecurity.vault_key_configured ? (
                  <span
                    data-vault-key-status="configured"
                    className="inline-flex shrink-0 items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                  >
                    <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500 dark:bg-emerald-400" />
                    Configured
                  </span>
                ) : (
                  <span
                    data-vault-key-status="not-set"
                    className="inline-flex shrink-0 items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
                  >
                    <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-amber-500 dark:bg-amber-400" />
                    Not set
                  </span>
                )}
              </div>
            </div>
          </section>
        )}

        {loadState === "loading" && (
          <p
            className="text-xs text-zinc-500 dark:text-zinc-400"
            data-platform-settings-loading
          >
            Loading integrations…
          </p>
        )}

        {loadState === "error" && (
          <div className="flex flex-col gap-2" data-platform-settings-error>
            <p role="alert" className="text-xs text-red-700 dark:text-red-300">
              {loadError ?? "Failed to load integrations"}
            </p>
            <button
              type="button"
              onClick={() => void load()}
              className="self-start rounded border border-zinc-300 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
            >
              Retry
            </button>
          </div>
        )}

        {loadState === "ready" && integrations.length === 0 && (
          <p
            className="text-xs text-zinc-500 dark:text-zinc-400"
            data-platform-settings-empty
          >
            No integrations available.
          </p>
        )}

        {loadState === "ready" &&
          grouped.map((group) => (
            <section
              key={group.category}
              data-integration-category={group.category}
              className="flex flex-col gap-2"
            >
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-zinc-400 dark:text-zinc-500">
                {group.category}
              </h3>
              <ul className="flex flex-col gap-2 list-none p-0">
                {group.items.map((it) => (
                  <IntegrationCard key={it.id} integration={it} />
                ))}
              </ul>
            </section>
          ))}
      </div>
    </section>
  );
}
