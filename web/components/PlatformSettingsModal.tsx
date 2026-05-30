"use client";

// PlatformSettingsModal — Kanban #1655 FE.
//
// Platform-wide "Integrations" popup. Lists OPTIONAL integrations (each OFF by
// default). Toggling one ON triggers VERIFY:
//   - integration's required key(s) configured  -> "Ready" badge.
//   - NOT configured                             -> inline setup panel with
//     ordered steps + doc links + the EXACT .env var NAMES to add (+ a
//     "restart required" note).
//
// There is NO key-entry field — keys live in .env; this UI shows STATUS +
// GUIDANCE only. The contract (api.ts getIntegrations / setIntegrationEnabled)
// returns env-var PRESENCE (`present: bool`), never a value, so this component
// can render "configured / not configured" without ever touching a secret.
// We deliberately render only `env_var.name` (+ required flag + presence dot)
// and never any value.
//
// Modal chrome (dialog role + backdrop + ESC close + mobile full-screen sheet /
// sm-centered card + 44px tap targets) is copy-adapted from EditProjectModal
// (#943). The Switch component (#1288) is a confirmation-trigger pattern (click
// opens a modal) — NOT a direct state toggle — so we build a small accessible
// inline toggle here (role="switch" + aria-checked) that flips on click with
// optimistic UI + rollback on PATCH error.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getIntegrations,
  setIntegrationEnabled,
  type Integration,
  type PlatformSecurity,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { Icon } from "./Icon";

// ---------------------------------------------------------------------------
// IntegrationToggle — small accessible on/off switch. Direct-flip semantics
// (unlike the project-control Switch which opens a modal). Disabled while a
// PATCH is in flight for that row so the operator can't double-fire.
// ---------------------------------------------------------------------------
function IntegrationToggle({
  checked,
  busy,
  onToggle,
  ariaLabel,
  dataId,
}: {
  checked: boolean;
  busy: boolean;
  onToggle: () => void;
  ariaLabel: string;
  dataId: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={busy}
      onClick={onToggle}
      data-integration-toggle={dataId}
      className="inline-flex shrink-0 items-center justify-center rounded-full p-2 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:p-0 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {/* Track */}
      <span
        aria-hidden
        className={`relative inline-flex h-4 w-7 shrink-0 rounded-full transition-colors ${
          checked
            ? "bg-emerald-500 dark:bg-emerald-400"
            : "bg-zinc-300 dark:bg-zinc-600"
        }`}
      >
        {/* Thumb */}
        <span
          className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-3.5" : "translate-x-0.5"
          }`}
        />
      </span>
    </button>
  );
}

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
// SetupPanel — guidance shown when an integration is enabled but not yet
// configured. Lists ordered steps, external doc links, and the EXACT .env var
// names (required ones flagged) plus a "restart required" note. Never renders
// any value — presence-only.
// ---------------------------------------------------------------------------
function SetupPanel({ integration }: { integration: Integration }) {
  const { steps, links } = integration.setup;
  return (
    <div
      data-integration-setup={integration.id}
      className="mt-2 flex flex-col gap-3 rounded border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-900/60 dark:bg-amber-950/20"
    >
      <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
        Setup required
      </p>

      {steps.length > 0 && (
        <ol className="ml-4 list-decimal space-y-1 text-xs text-zinc-700 dark:text-zinc-300">
          {steps.map((step, i) => (
            <li key={i}>{step}</li>
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
                  <span className="rounded bg-zinc-100 px-1 text-[9px] font-semibold uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
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

      <p className="text-[11px] italic text-amber-700 dark:text-amber-400">
        Restart required after editing <code className="font-mono not-italic">.env</code>.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// IntegrationCard — one integration row.
// ---------------------------------------------------------------------------
function IntegrationCard({
  integration,
  busy,
  onToggle,
}: {
  integration: Integration;
  busy: boolean;
  onToggle: (next: boolean) => void;
}) {
  const showSetup = integration.enabled && !integration.configured;
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
            {integration.enabled && integration.configured && (
              <span className="text-[10px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
                Enabled
              </span>
            )}
          </div>
        </div>
        <IntegrationToggle
          checked={integration.enabled}
          busy={busy}
          onToggle={() => onToggle(!integration.enabled)}
          dataId={integration.id}
          ariaLabel={
            integration.enabled
              ? `Disable ${integration.label}`
              : `Enable ${integration.label}`
          }
        />
      </div>
      {showSetup && <SetupPanel integration={integration} />}
    </li>
  );
}

// ---------------------------------------------------------------------------
// PlatformSettingsModal
// ---------------------------------------------------------------------------
type LoadState = "idle" | "loading" | "ready" | "error";

export function PlatformSettingsModal() {
  const [open, setOpen] = useState(false);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [platformSecurity, setPlatformSecurity] = useState<PlatformSecurity | null>(null);
  // Per-row in-flight PATCH guard (set of integration ids).
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  // Non-blocking toggle-error banner (rollback already happened by the time
  // this shows). Cleared on next successful toggle / re-open.
  const [toggleError, setToggleError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

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

  // Fetch on open. Reset transient state on close.
  useEffect(() => {
    if (!open) return;
    setToggleError(null);
    void load();
    requestAnimationFrame(() => closeRef.current?.focus());
  }, [open, load]);

  // ESC closes (mirrors EditProjectModal).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Optimistic toggle with rollback on PATCH error. The PATCH response carries
  // refreshed `configured` + env-var presence, so we replace the row with the
  // server's view on success (the inline setup panel re-renders from it).
  const onToggle = useCallback(
    async (id: string, next: boolean) => {
      const original = integrations.find((it) => it.id === id);
      if (!original) return;
      // Guard against double-fire while a PATCH is already in flight.
      if (busyIds.has(id)) return;

      setToggleError(null);
      setBusyIds((prev) => new Set(prev).add(id));
      // Optimistic flip.
      setIntegrations((prev) =>
        prev.map((it) => (it.id === id ? { ...it, enabled: next } : it)),
      );

      try {
        const updated = await setIntegrationEnabled(id, next);
        setIntegrations((prev) =>
          prev.map((it) => (it.id === id ? updated : it)),
        );
      } catch (err: unknown) {
        // Rollback to the pre-toggle row.
        setIntegrations((prev) =>
          prev.map((it) => (it.id === id ? original : it)),
        );
        setToggleError(`${original.label}: ${extractErrorMessage(err, "toggle failed")}`);
      } finally {
        setBusyIds((prev) => {
          const nextSet = new Set(prev);
          nextSet.delete(id);
          return nextSet;
        });
      }
    },
    [integrations, busyIds],
  );

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
    <>
      {/* Gear trigger — matches EditProjectModal icon-button styling + 44px
          mobile tap target. `agent-config` is the gear glyph in the sprite. */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Platform integrations settings"
        title="Integrations"
        className="inline-flex items-center justify-center rounded border border-zinc-300 bg-white text-zinc-700 hover:border-zinc-400 hover:text-zinc-900 px-2 py-1 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:px-1.5 sm:py-1 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:text-zinc-100"
        data-platform-settings-trigger
      >
        <Icon name="agent-config" size={14} />
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="platform-settings-title"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-zinc-900/40 dark:bg-zinc-950/70 sm:items-center sm:px-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setOpen(false);
          }}
          data-platform-settings-modal
        >
          <div className="flex w-full max-w-none flex-col overflow-y-auto rounded-none border-0 bg-white p-4 dark:bg-zinc-900 sm:h-auto sm:max-h-[85vh] sm:max-w-lg sm:rounded sm:border sm:border-zinc-200 sm:dark:border-zinc-800">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-col gap-1">
                <h2
                  id="platform-settings-title"
                  className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
                >
                  Integrations
                </h2>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Optional platform integrations. Enable one to verify its keys.
                  Keys live in <code className="font-mono">.env</code> — this panel shows status only.
                </p>
              </div>
              <button
                ref={closeRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close integrations settings"
                className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-platform-settings-close
              >
                ✕
              </button>
            </div>

            {/* Non-blocking toggle-error banner (rollback already applied). */}
            {toggleError !== null && (
              <p
                role="alert"
                className="mt-3 rounded border border-red-300 bg-red-50 px-2 py-1.5 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
                data-platform-settings-toggle-error
              >
                {toggleError}
              </p>
            )}

            <div className="mt-3 flex flex-col gap-4">
              {/* Platform security — Kanban #1658. Always shown (not a toggle).
                  Placed at the top so the operator sees vault status before
                  any optional integration. Never renders a key value — only
                  a presence boolean from the API. */}
              {loadState === "ready" && platformSecurity !== null && (
                <section
                  data-platform-security
                  className="flex flex-col gap-2"
                >
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
                <div
                  className="flex flex-col gap-2"
                  data-platform-settings-error
                >
                  <p
                    role="alert"
                    className="text-xs text-red-700 dark:text-red-300"
                  >
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
                        <IntegrationCard
                          key={it.id}
                          integration={it}
                          busy={busyIds.has(it.id)}
                          onToggle={(next) => void onToggle(it.id, next)}
                        />
                      ))}
                    </ul>
                  </section>
                ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
