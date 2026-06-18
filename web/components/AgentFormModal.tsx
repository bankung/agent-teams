"use client";

// AgentFormModal — Kanban #2481. Create + edit surface for a Claude Code agent
// definition (.claude/agents/*.md), driving the operator-gated write endpoints:
//   create → POST /api/agents          (201 AgentSummary; 409 if name exists)
//   edit   → PUT  /api/agents/{name}   (200 AgentSummary; 404 if absent)
//
// Modal chrome reuses ModalShell (scrollable — this is the densest form in the
// app: a markdown `body` textarea + an operator-token field + the field set).
// The same component serves both modes (`mode` prop); edit pre-fills from the
// AgentDetail the page already fetched (no extra round-trip on open).
//
// ── Operator-token security (read this before touching the token state) ──
// The write endpoints are gated by X-Operator-Token (the operator's
// OPERATOR_ACTION_KEY). The token is held in LOCAL component state ONLY:
//   - never written to localStorage / sessionStorage / cookies / a NEXT_PUBLIC_*
//     var / the bundle;
//   - cleared on close + on a successful save (resetForm);
//   - passed per-call to create/updateAgent, which stamp the header only when a
//     non-empty token is present.
// When the gate is dormant (key unset server-side) a token-less save still
// works — we do NOT hard-require the field client-side; the SERVER decides. A
// 403 surfaces the "paste your OPERATOR_ACTION_KEY" prompt.
//
// ── Validation philosophy ──
// Client-side we mirror the name regex + required(description) for fast feedback
// and a disabled submit. The SERVER (Pydantic AgentWrite + the file validator)
// is the authority; a 422 with `{message, diagnostics[]}` renders the per-field
// diagnostics list (extractAgentWriteDiagnostics), a plain-string 422 (bad name /
// name-mismatch) renders its message, 409 / 404 / 403 render targeted copy.

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  AGENT_NAME_PATTERN,
  AGENT_NAME_RE,
} from "@/lib/agentName";
import {
  createAgent,
  updateAgent,
  extractAgentWriteDiagnostics,
  HttpError,
  type AgentDetail,
  type AgentModelTier,
  type AgentValidationError,
  type AgentWrite,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { ModalShell } from "./ModalShell";

const MODEL_TIERS: AgentModelTier[] = ["opus", "sonnet", "haiku"];

// Tools-field mode: an explicit list, the literal "All tools", or absent
// (inherit — the file omits the `tools:` key entirely).
type ToolsMode = "all" | "list" | "inherit";

// SaveError — normalised result of a failed write. `diagnostics` is the
// validator's per-field list (422 object detail); otherwise null and `message`
// carries the human text. `kind` tags the well-known codes for targeted copy.
type SaveError = {
  kind: "operator" | "conflict" | "notfound" | "validation" | "other";
  message: string;
  diagnostics: AgentValidationError[] | null;
};

type Props =
  | {
      mode: "create";
      open: boolean;
      onClose: () => void;
      agent?: undefined;
    }
  | {
      mode: "edit";
      open: boolean;
      onClose: () => void;
      // Pre-fill source — the AgentDetail the /agents/[name] page already has.
      agent: AgentDetail;
    };

export function AgentFormModal(props: Props) {
  const { mode, open, onClose } = props;
  const router = useRouter();
  const isEdit = mode === "edit";

  // ── form state ──
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<"" | AgentModelTier>("");
  const [scope, setScope] = useState("");
  const [body, setBody] = useState("");
  // tools — mode + the editable list (one row per tool when mode==="list").
  const [toolsMode, setToolsMode] = useState<ToolsMode>("inherit");
  const [tools, setTools] = useState<string[]>([]);
  // hooks — advanced; raw JSON text (MVP). Empty = no hooks key. The parse
  // error is derived from `parsedHooks` (useMemo) and rendered directly — no
  // separate error state to keep in sync.
  const [hooksText, setHooksText] = useState("");
  // operator token — in-memory ONLY (see file header). Never persisted.
  const [operatorToken, setOperatorToken] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [saveError, setSaveError] = useState<SaveError | null>(null);

  const firstFieldRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(
    null,
  );

  // Stable primitive key for the edit source — the agent NAME (its identity /
  // filename). Depending on this (not the object) keeps the pre-fill effect
  // from re-running on every render when the parent passes a fresh-but-equal
  // `agent` object (which would otherwise wipe in-progress field edits incl.
  // the operator token). Null in create mode.
  const editAgent = isEdit ? props.agent : null;
  const editAgentName = editAgent?.name ?? null;

  // Pre-fill (edit) / blank (create) on open. Re-runs when `open` flips true or
  // the source agent NAME changes (a genuinely different agent). Closed state
  // stays put. Reads the latest `editAgent` snapshot without depending on its
  // object identity.
  const editAgentRef = useRef(editAgent);
  editAgentRef.current = editAgent;
  useEffect(() => {
    if (!open) return;
    const a = editAgentRef.current;
    if (isEdit && a) {
      setName(a.name);
      setDescription(a.full_description ?? a.description ?? "");
      setModel(a.model ?? "");
      // Pre-fill tools from a.tools (Kanban #2481 finish).
      //   null        → no `tools:` key → inherit mode, empty list
      //   "All tools" → all mode
      //   string[]    → list mode, pre-populate rows
      if (a.tools === null) {
        setToolsMode("inherit");
        setTools([]);
      } else if (a.tools === "All tools") {
        setToolsMode("all");
        setTools([]);
      } else {
        setToolsMode("list");
        setTools(a.tools);
      }
      setScope("");
      setHooksText("");
      // Pre-fill body from a.body (Kanban #2481 finish).
      setBody(a.body ?? "");
    } else {
      setName("");
      setDescription("");
      setModel("");
      setScope("");
      setBody("");
      setToolsMode("inherit");
      setTools([]);
      setHooksText("");
    }
    // Token is ALWAYS cleared on open — never carried between sessions.
    setOperatorToken("");
    setSaveError(null);
    requestAnimationFrame(() => firstFieldRef.current?.focus());
    // editAgentName (a string) is the stable edit identity; editAgentRef gives
    // the latest snapshot without re-running on object-identity churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, isEdit, editAgentName]);

  function resetForm() {
    setName("");
    setDescription("");
    setModel("");
    setScope("");
    setBody("");
    setToolsMode("inherit");
    setTools([]);
    setHooksText("");
    setOperatorToken("");
    setSaveError(null);
  }

  function closeModal() {
    if (submitting) return;
    resetForm();
    onClose();
  }

  // ── client-side validity (mirror of the server contract; server is final) ──
  const trimmedName = name.trim();
  const nameValid = AGENT_NAME_RE.test(trimmedName);
  const descriptionValid = description.trim().length > 0;

  // hooks JSON must parse to an object when non-empty.
  const parsedHooks = useMemo<
    { ok: true; value: Record<string, unknown> | null } | { ok: false; error: string }
  >(() => {
    const raw = hooksText.trim();
    if (raw === "") return { ok: true, value: null };
    try {
      const v = JSON.parse(raw);
      if (v === null || typeof v !== "object" || Array.isArray(v)) {
        return { ok: false, error: "Hooks must be a JSON object (e.g. { \"PreToolUse\": [...] })." };
      }
      return { ok: true, value: v as Record<string, unknown> };
    } catch (e) {
      return { ok: false, error: `Hooks JSON is invalid: ${e instanceof Error ? e.message : "parse error"}` };
    }
  }, [hooksText]);

  const canSubmit =
    !submitting && nameValid && descriptionValid && parsedHooks.ok;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    // canSubmit already requires parsedHooks.ok, so the hooks value below is
    // safe to read; this guard also blocks an Enter-submit on an invalid form.
    if (!canSubmit || !parsedHooks.ok) return;

    // Build the minimal AgentWrite. Optional keys are sent only when set, so
    // the BE writes only the frontmatter keys the operator actually chose
    // (model/tools/hooks/scope absent = inherit/omit).
    const payload: AgentWrite = {
      name: trimmedName,
      description: description.trim(),
      body, // verbatim; may be ""
    };
    if (model !== "") payload.model = model;
    if (toolsMode === "all") {
      payload.tools = "All tools";
    } else if (toolsMode === "list") {
      const cleaned = tools.map((t) => t.trim()).filter((t) => t.length > 0);
      if (cleaned.length > 0) payload.tools = cleaned;
      // an empty list in "list" mode = inherit (omit the key).
    }
    if (parsedHooks.value !== null) payload.hooks = parsedHooks.value;
    if (scope.trim() !== "") payload.scope = scope.trim();

    setSubmitting(true);
    setSaveError(null);
    try {
      if (isEdit) {
        // Path is authoritative; name is disabled in edit so body name === path.
        await updateAgent(props.agent.name, payload, operatorToken);
      } else {
        await createAgent(payload, operatorToken);
      }
      // Refresh the gallery / detail (server components re-fetch) and close.
      router.refresh();
      resetForm();
      onClose();
    } catch (err: unknown) {
      setSaveError(toSaveError(err, isEdit));
    } finally {
      setSubmitting(false);
    }
  }

  // tools list mutators (list mode).
  function addTool() {
    setTools((prev) => [...prev, ""]);
  }
  function updateTool(idx: number, value: string) {
    setTools((prev) => prev.map((t, i) => (i === idx ? value : t)));
  }
  function removeTool(idx: number) {
    setTools((prev) => prev.filter((_, i) => i !== idx));
  }

  const inputCls =
    "mt-1 block w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500 dark:focus:border-zinc-500";
  const labelCls =
    "mt-3 block text-xs font-medium text-zinc-700 dark:text-zinc-300";

  return (
    <ModalShell
      open={open}
      onClose={closeModal}
      labelledBy="agent-form-title"
      maxWidth="lg"
      scrollable
      backdropProps={{
        "data-agent-form-modal": true,
        "data-agent-form-mode": mode,
      }}
    >
      <form onSubmit={onSubmit}>
        <h2
          id="agent-form-title"
          className="text-sm font-semibold uppercase tracking-wide text-zinc-900 dark:text-zinc-100"
        >
          {isEdit ? (
            <>
              Edit agent ·{" "}
              <span className="font-mono normal-case">{props.agent.name}</span>
            </>
          ) : (
            "New agent"
          )}
        </h2>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Writes <span className="font-mono">.claude/agents/{trimmedName || "<name>"}.md</span>.
          {isEdit ? " Name is the filename and cannot change here." : ""}
        </p>

        {/* Restart caveat — prominent callout (AC). */}
        <div
          data-agent-form-restart-note
          role="note"
          className="mt-3 flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800 dark:border-amber-700/60 dark:bg-amber-900/30 dark:text-amber-200"
        >
          <span aria-hidden className="mt-px font-semibold">⚠</span>
          <span>
            A new or edited agent is <strong>not invokable until Claude Code restarts</strong>{" "}
            — agent files load at session start.
          </span>
        </div>

        {/* name — disabled in edit (identity / filename). */}
        <label className={labelCls}>
          Name <span className="text-red-600 dark:text-red-400">*</span>
          <input
            ref={!isEdit ? (firstFieldRef as React.RefObject<HTMLInputElement>) : undefined}
            type="text"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (saveError !== null) setSaveError(null);
            }}
            placeholder="dev-frontend"
            autoComplete="off"
            spellCheck={false}
            disabled={submitting || isEdit}
            aria-invalid={trimmedName.length > 0 && !nameValid}
            className={`${inputCls} font-mono`}
            data-agent-form-name
          />
          {trimmedName.length > 0 && !nameValid ? (
            <span
              role="alert"
              data-agent-form-name-error
              className="mt-1 block text-[10px] text-red-700 dark:text-red-300"
            >
              Lower-case alphanumeric segments joined by single hyphens (regex{" "}
              <span className="font-mono">{AGENT_NAME_PATTERN}</span>).
            </span>
          ) : null}
        </label>

        {/* description — required. */}
        <label className={labelCls}>
          Description <span className="text-red-600 dark:text-red-400">*</span>
          <textarea
            ref={isEdit ? (firstFieldRef as React.RefObject<HTMLTextAreaElement>) : undefined}
            value={description}
            onChange={(e) => {
              setDescription(e.target.value);
              if (saveError !== null) setSaveError(null);
            }}
            rows={3}
            placeholder="One-paragraph trigger description Claude Code reads to route work to this agent."
            disabled={submitting}
            aria-invalid={description.length > 0 && !descriptionValid}
            className={inputCls}
            data-agent-form-description
          />
        </label>

        {/* model — tier select (reuses the known tiers). */}
        <label className={labelCls}>
          Model tier
          <select
            value={model}
            onChange={(e) => {
              setModel(e.target.value as "" | AgentModelTier);
              if (saveError !== null) setSaveError(null);
            }}
            disabled={submitting}
            className={inputCls}
            data-agent-form-model
          >
            <option value="">Inherit (no model key)</option>
            {MODEL_TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        {/* tools — inherit / All tools / explicit list. */}
        <fieldset className="mt-3 rounded border border-zinc-200 p-2 dark:border-zinc-800">
          <legend className="px-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Tools
          </legend>
          <div className="flex flex-wrap items-center gap-3 text-xs text-zinc-700 dark:text-zinc-300">
            {(
              [
                { v: "inherit" as const, label: "Inherit (all)" },
                { v: "all" as const, label: '"All tools"' },
                { v: "list" as const, label: "Explicit list" },
              ]
            ).map((opt) => (
              <label key={opt.v} className="inline-flex items-center gap-1.5">
                <input
                  type="radio"
                  name="agent-form-tools-mode"
                  value={opt.v}
                  checked={toolsMode === opt.v}
                  onChange={() => {
                    setToolsMode(opt.v);
                    if (saveError !== null) setSaveError(null);
                  }}
                  disabled={submitting}
                  data-agent-form-tools-mode={opt.v}
                />
                <span>{opt.label}</span>
              </label>
            ))}
          </div>
          {toolsMode === "list" ? (
            <div className="mt-2 flex flex-col gap-2" data-agent-form-tools-list>
              {tools.length === 0 ? (
                <p className="text-[11px] text-zinc-500 dark:text-zinc-500">
                  No tools yet — add the tool names this agent may use (e.g. Read, Grep, Bash).
                </p>
              ) : (
                tools.map((t, idx) => (
                  <div key={idx} className="flex items-center gap-2">
                    <input
                      type="text"
                      value={t}
                      onChange={(e) => updateTool(idx, e.target.value)}
                      placeholder="Read"
                      autoComplete="off"
                      spellCheck={false}
                      disabled={submitting}
                      className={`${inputCls} mt-0 font-mono`}
                      data-agent-form-tool-row={idx}
                    />
                    <button
                      type="button"
                      onClick={() => removeTool(idx)}
                      disabled={submitting}
                      aria-label={`Remove tool ${idx + 1}`}
                      className="shrink-0 rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500 hover:border-red-400 hover:text-red-700 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-700 dark:hover:text-red-300"
                      data-agent-form-tool-remove={idx}
                    >
                      Remove
                    </button>
                  </div>
                ))
              )}
              <button
                type="button"
                onClick={addTool}
                disabled={submitting}
                className="self-start rounded border border-zinc-200 bg-white px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
                data-agent-form-tool-add
              >
                + Add tool
              </button>
            </div>
          ) : null}
        </fieldset>

        {/* scope — optional free text. */}
        <label className={labelCls}>
          Scope <span className="font-normal text-zinc-400">(optional)</span>
          <input
            type="text"
            value={scope}
            onChange={(e) => {
              setScope(e.target.value);
              if (saveError !== null) setSaveError(null);
            }}
            placeholder="e.g. read-only review of sensitive surfaces"
            autoComplete="off"
            disabled={submitting}
            className={inputCls}
            data-agent-form-scope
          />
        </label>

        {/* hooks — advanced JSON (MVP). */}
        <label className={labelCls}>
          Hooks <span className="font-normal text-zinc-400">(optional · JSON object)</span>
          <textarea
            value={hooksText}
            onChange={(e) => {
              setHooksText(e.target.value);
              if (saveError !== null) setSaveError(null);
            }}
            rows={3}
            placeholder='{ "PreToolUse": [ { "matcher": "Bash", "hooks": [...] } ] }'
            spellCheck={false}
            disabled={submitting}
            aria-invalid={!parsedHooks.ok}
            className={`${inputCls} font-mono`}
            data-agent-form-hooks
          />
          {!parsedHooks.ok ? (
            <span
              role="alert"
              data-agent-form-hooks-error
              className="mt-1 block text-[10px] text-red-700 dark:text-red-300"
            >
              {parsedHooks.error}
            </span>
          ) : null}
        </label>

        {/* body — markdown; monospace, generous height. */}
        <label className={labelCls}>
          Body <span className="font-normal text-zinc-400">(markdown · the agent prompt)</span>
          <textarea
            value={body}
            onChange={(e) => {
              setBody(e.target.value);
              if (saveError !== null) setSaveError(null);
            }}
            rows={12}
            placeholder={"You are a …\n\n## What you do\n- …"}
            spellCheck={false}
            disabled={submitting}
            className={`${inputCls} font-mono leading-relaxed`}
            data-agent-form-body
          />
        </label>

        {/* operator token — in-memory only; type=password; autocomplete off. */}
        <fieldset className="mt-4 rounded border border-zinc-300 bg-zinc-50/60 p-2 dark:border-zinc-700 dark:bg-zinc-900/40">
          <legend className="px-1 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:text-zinc-300">
            Authorization
          </legend>
          <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Operator token
            <input
              type="password"
              value={operatorToken}
              onChange={(e) => {
                setOperatorToken(e.target.value);
                if (saveError !== null) setSaveError(null);
              }}
              placeholder="OPERATOR_ACTION_KEY — paste to authorize this write"
              autoComplete="off"
              spellCheck={false}
              disabled={submitting}
              className={`${inputCls} font-mono`}
              data-agent-form-operator-token
            />
          </label>
          <p className="mt-1 text-[10px] text-zinc-500 dark:text-zinc-500">
            Held in memory for this save only — never stored. Required when the
            server-side write gate is active.
          </p>
        </fieldset>

        {/* save error — diagnostics list OR a single message, per status code. */}
        {saveError !== null ? (
          <div
            role="alert"
            data-agent-form-error
            data-agent-form-error-kind={saveError.kind}
            className="mt-3 rounded border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
          >
            <p className="font-medium">{saveError.message}</p>
            {saveError.diagnostics && saveError.diagnostics.length > 0 ? (
              <ul
                data-agent-form-diagnostics
                className="mt-1.5 flex flex-col gap-1"
              >
                {saveError.diagnostics.map((d, i) => (
                  <li
                    key={`${d.field}:${d.line}:${i}`}
                    data-agent-form-diagnostic
                    data-severity={d.severity}
                    className="flex flex-wrap items-center gap-1.5 rounded bg-white/60 px-1.5 py-1 dark:bg-zinc-950/40"
                  >
                    <span
                      className={`inline-flex shrink-0 items-center rounded px-1 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
                        d.severity === "error"
                          ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-200"
                          : "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-200"
                      }`}
                    >
                      {d.severity}
                    </span>
                    <span className="font-mono text-[10px] text-zinc-500 dark:text-zinc-400">
                      {d.field}
                      {d.line ? `:${d.line}` : ""}
                    </span>
                    <span>{d.message}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {/* actions */}
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={closeModal}
            disabled={submitting}
            className="rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            data-agent-form-cancel
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="rounded border border-emerald-600 bg-emerald-600 px-3 py-2 text-xs font-medium uppercase tracking-wide text-white hover:bg-emerald-700 disabled:opacity-50 min-h-[44px] sm:min-h-0 sm:px-2 sm:py-1 dark:border-emerald-500 dark:bg-emerald-500 dark:hover:bg-emerald-600"
            data-agent-form-submit
          >
            {submitting
              ? isEdit
                ? "Saving…"
                : "Creating…"
              : isEdit
                ? "Save"
                : "Create"}
          </button>
        </div>
      </form>
    </ModalShell>
  );
}

// toSaveError — map a caught write error to the rendered SaveError. 422 with an
// object detail → the validator diagnostics list; 422 string detail → its
// message; 403/409/404 → targeted operator-readable copy.
function toSaveError(err: unknown, isEdit: boolean): SaveError {
  if (err instanceof HttpError) {
    if (err.status === 403) {
      return {
        kind: "operator",
        message:
          "Operator token required or invalid — paste your OPERATOR_ACTION_KEY to authorize this write.",
        diagnostics: null,
      };
    }
    if (err.status === 409) {
      return {
        kind: "conflict",
        message:
          "An agent with that name already exists. Pick a different name, or edit the existing agent.",
        diagnostics: null,
      };
    }
    if (err.status === 404 && isEdit) {
      return {
        kind: "notfound",
        message:
          "That agent no longer exists — it may have been deleted. Reload the gallery.",
        diagnostics: null,
      };
    }
    if (err.status === 422) {
      const diag = extractAgentWriteDiagnostics(err.detail);
      if (diag) {
        return {
          kind: "validation",
          message: diag.message,
          diagnostics: diag.diagnostics,
        };
      }
      // Plain-string 422 (bad name shape / PUT name mismatch / Pydantic field).
      return { kind: "validation", message: err.message, diagnostics: null };
    }
    return { kind: "other", message: err.message, diagnostics: null };
  }
  return {
    kind: "other",
    message: extractErrorMessage(err, isEdit ? "Edit failed" : "Create failed"),
    diagnostics: null,
  };
}
