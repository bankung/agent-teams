"use client";

// Kanban #2181 — editable acceptance-criteria section for TaskDetail.
// Extracted to keep TaskDetail.tsx growth under +250 LOC.
//
// Props:
//   criteria      — current server value (null treated as [])
//   isTerminal    — when true, render read-only (ps=5 or ps=6)
//   onSave        — called with the mutated full array; caller does the PATCH
//   disabled      — set while a parent save is in flight

import { useState, useEffect, useRef, useCallback } from "react";
import type { AcceptanceCriterion } from "@/lib/api";

// Badge config shared with the read-only renderer below
const AC_STATUS_BADGE: Record<
  AcceptanceCriterion["status"],
  { glyph: string; className: string; label: string }
> = {
  passed: {
    glyph: "✓",
    className: "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300",
    label: "passed",
  },
  failed: {
    glyph: "✗",
    className: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300",
    label: "failed",
  },
  pending: {
    glyph: "·",
    className: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    label: "pending",
  },
  na: {
    glyph: "—",
    className: "bg-zinc-50 text-zinc-400 dark:bg-zinc-900 dark:text-zinc-500",
    label: "n/a",
  },
};

const STATUS_OPTIONS: AcceptanceCriterion["status"][] = [
  "pending",
  "passed",
  "failed",
  "na",
];

type Props = {
  criteria: AcceptanceCriterion[] | null;
  isTerminal: boolean;
  onSave: (updated: AcceptanceCriterion[]) => Promise<void>;
  disabled?: boolean;
  onToast?: (msg: string) => void;
};

export function AcEditor({ criteria, isTerminal, onSave, disabled = false, onToast }: Props) {
  const serverList = criteria ?? [];
  const total = serverList.length;
  const passed = serverList.filter((c) => c.status === "passed").length;
  const headerLabel =
    total > 0 ? `Acceptance criteria (${passed}/${total})` : "Acceptance criteria";

  // ── edit mode state ──────────────────────────────────────────────────────────
  // Draft is keyed off edit-mode only; syncs from server only when not editing.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<AcceptanceCriterion[]>([]);
  const [saving, setSaving] = useState(false);
  // Per-item validation: indices with empty text
  const [emptyIndices, setEmptyIndices] = useState<Set<number>>(new Set());

  // ── quick-edit state (mobile long-press) ─────────────────────────────────────
  const [quickEditIdx, setQuickEditIdx] = useState<number | null>(null);
  const [quickEditText, setQuickEditText] = useState("");
  const [quickSaving, setQuickSaving] = useState(false);
  const [quickSavedMsg, setQuickSavedMsg] = useState<string | null>(null);
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const LONG_PRESS_MS = 500;

  const enterQuickEdit = useCallback((idx: number) => {
    setQuickEditIdx(idx);
    setQuickEditText(serverList[idx]?.text ?? "");
    setQuickSavedMsg(null);
   
  }, [serverList]);

  function clearLongPress() {
    if (longPressTimer.current !== null) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  }

  function makeTouchHandlers(idx: number) {
    return {
      onTouchStart: () => {
        clearLongPress();
        longPressTimer.current = setTimeout(() => {
          enterQuickEdit(idx);
        }, LONG_PRESS_MS);
      },
      onTouchEnd: clearLongPress,
      onTouchMove: clearLongPress,
      onTouchCancel: clearLongPress,
    };
  }

  async function handleQuickBlur() {
    if (quickEditIdx === null) return;
    const original = serverList[quickEditIdx]?.text ?? "";
    if (quickEditText === original) {
      setQuickEditIdx(null);
      return;
    }
    setQuickSaving(true);
    try {
      const updated = serverList.map((c, i) =>
        i === quickEditIdx ? { ...c, text: quickEditText } : c
      );
      await onSave(updated);
      setQuickEditIdx(null);
      const msg = "AC updated";
      if (onToast) {
        onToast(msg);
      } else {
        setQuickSavedMsg(msg);
        setTimeout(() => setQuickSavedMsg(null), 2000);
      }
    } catch {
      // Keep textarea open so edit isn't lost; surface error
      const errMsg = "Save failed — try again";
      if (onToast) {
        onToast(errMsg);
      } else {
        setQuickSavedMsg(errMsg);
      }
    } finally {
      setQuickSaving(false);
    }
  }

  // SSE-refresh guard: only sync from server when NOT in edit mode
  useEffect(() => {
    if (!editing) {
      setDraft(serverList.map((c) => ({ ...c })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [criteria, editing]);

  function openEdit() {
    setDraft(serverList.map((c) => ({ ...c })));
    setEmptyIndices(new Set());
    setEditing(true);
  }

  function handleCancel() {
    setDraft(serverList.map((c) => ({ ...c })));
    setEmptyIndices(new Set());
    setEditing(false);
  }

  async function handleSave() {
    // Block save when any item has empty text
    const bad = new Set<number>();
    draft.forEach((item, i) => {
      if (item.text.trim() === "") bad.add(i);
    });
    if (bad.size > 0) {
      setEmptyIndices(bad);
      return;
    }
    setSaving(true);
    try {
      await onSave(draft);
      setEditing(false);
      setEmptyIndices(new Set());
    } finally {
      setSaving(false);
    }
  }

  function updateItemText(idx: number, text: string) {
    setDraft((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], text };
      return next;
    });
    if (text.trim() !== "") {
      setEmptyIndices((prev) => {
        const next = new Set(prev);
        next.delete(idx);
        return next;
      });
    }
  }

  function updateItemStatus(idx: number, status: AcceptanceCriterion["status"]) {
    setDraft((prev) => {
      const next = [...prev];
      const item = { ...next[idx] };
      const wasPending = item.status === "pending";
      const goingPending = status === "pending";

      if (goingPending) {
        // Clear verification stamps
        item.verified_by = null;
        item.verified_at = null;
      } else if (wasPending || item.status !== status) {
        // Transitioning to or between non-pending: stamp operator + now
        item.verified_by = "operator";
        item.verified_at = new Date().toISOString();
      }
      item.status = status;
      next[idx] = item;
      return next;
    });
  }

  function addItem() {
    setDraft((prev) => [
      ...prev,
      { text: "", status: "pending", verified_by: null, verified_at: null, notes: null },
    ]);
  }

  function removeItem(idx: number) {
    if (!window.confirm("Remove this acceptance criterion?")) return;
    setDraft((prev) => prev.filter((_, i) => i !== idx));
    setEmptyIndices((prev) => {
      const next = new Set<number>();
      prev.forEach((i) => { if (i < idx) next.add(i); else if (i > idx) next.add(i - 1); });
      return next;
    });
  }

  // ── read-only render ─────────────────────────────────────────────────────────
  if (isTerminal || !editing) {
    const canQuickEdit = !isTerminal && !disabled;
    return (
      <section className="flex flex-col gap-2" data-acceptance-criteria>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {headerLabel}
          </h3>
          {!isTerminal && (
            <button
              type="button"
              onClick={openEdit}
              disabled={disabled}
              aria-label="Edit acceptance criteria"
              data-ac-edit-trigger
              className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-800 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-600 dark:hover:text-zinc-200"
            >
              Edit
            </button>
          )}
        </div>
        {quickSavedMsg && (
          <p className="text-xs text-green-600 dark:text-green-400" role="status">
            {quickSavedMsg}
          </p>
        )}
        <div>
          {total === 0 ? (
            <p className="text-sm italic text-zinc-500 dark:text-zinc-400">
              (none defined)
            </p>
          ) : (
            <ol className="flex flex-col gap-1">
              {serverList.map((c, idx) => {
                const badge = AC_STATUS_BADGE[c.status];
                const isQuickEditing = quickEditIdx === idx;
                const touchHandlers = canQuickEdit ? makeTouchHandlers(idx) : {};
                return (
                  <li
                    key={idx}
                    className="flex gap-2 py-1.5"
                    data-ac-item
                    data-ac-status={c.status}
                    {...touchHandlers}
                  >
                    <span
                      aria-label={badge.label}
                      className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-xs font-semibold ${badge.className}`}
                    >
                      {badge.glyph}
                    </span>
                    <div className="flex-1">
                      {isQuickEditing ? (
                        <textarea
                          autoFocus
                          rows={2}
                          value={quickEditText}
                          onChange={(e) => setQuickEditText(e.target.value)}
                          onBlur={handleQuickBlur}
                          onKeyDown={(e) => {
                            if (e.key === "Escape") {
                              setQuickEditIdx(null);
                            }
                          }}
                          disabled={quickSaving}
                          data-ac-quickedit={idx}
                          className="w-full rounded border border-zinc-300 bg-white px-2 py-1 text-sm text-zinc-900 focus:outline-none disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100"
                        />
                      ) : (
                        <p className="whitespace-pre-wrap text-sm text-zinc-900 dark:text-zinc-100">
                          {c.text}
                        </p>
                      )}
                      {c.verified_by && (
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">
                          by {c.verified_by}
                          {c.verified_at && ` · ${c.verified_at}`}
                        </p>
                      )}
                      {c.notes && (
                        <p className="mt-1 whitespace-pre-wrap text-xs text-zinc-600 dark:text-zinc-400">
                          {c.notes}
                        </p>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </section>
    );
  }

  // ── edit render ──────────────────────────────────────────────────────────────
  return (
    <section className="flex flex-col gap-2" data-acceptance-criteria data-ac-editing>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {headerLabel}
        </h3>
      </div>
      <div className="flex flex-col gap-2 rounded border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-950/40">
        {draft.length === 0 && (
          <p className="text-xs italic text-zinc-400 dark:text-zinc-500">
            No criteria yet — add one below.
          </p>
        )}
        <ol className="flex flex-col gap-3" data-ac-draft-list>
          {draft.map((item, idx) => (
            <li key={idx} className="flex flex-col gap-1.5" data-ac-draft-item={idx}>
              <div className="flex items-start gap-2">
                {/* Text input */}
                <div className="flex-1">
                  <label
                    htmlFor={`ac-text-${idx}`}
                    className="sr-only"
                  >
                    Criterion {idx + 1} text
                  </label>
                  <textarea
                    id={`ac-text-${idx}`}
                    rows={2}
                    value={item.text}
                    onChange={(e) => updateItemText(idx, e.target.value)}
                    disabled={saving || disabled}
                    placeholder="Criterion text…"
                    data-ac-text-input={idx}
                    className={`w-full rounded border px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none disabled:opacity-50 dark:text-zinc-100 dark:placeholder-zinc-500 ${
                      emptyIndices.has(idx)
                        ? "border-red-400 bg-red-50 focus:border-red-500 dark:border-red-600 dark:bg-red-900/20"
                        : "border-zinc-300 bg-white focus:border-zinc-500 dark:border-zinc-700 dark:bg-zinc-900"
                    }`}
                  />
                  {emptyIndices.has(idx) && (
                    <p className="mt-0.5 text-xs text-red-600 dark:text-red-400" role="alert">
                      Text is required.
                    </p>
                  )}
                </div>
                {/* Remove button */}
                <button
                  type="button"
                  onClick={() => removeItem(idx)}
                  disabled={saving || disabled}
                  aria-label={`Remove criterion ${idx + 1}`}
                  data-ac-remove={idx}
                  className="mt-1 shrink-0 rounded border border-zinc-200 bg-white p-1 text-zinc-500 hover:border-red-300 hover:text-red-600 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-red-700 dark:hover:text-red-400"
                >
                  <svg aria-hidden xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="h-3.5 w-3.5">
                    <path d="M2.22 2.22a.75.75 0 0 1 1.06 0L8 6.94l4.72-4.72a.75.75 0 1 1 1.06 1.06L9.06 8l4.72 4.72a.75.75 0 1 1-1.06 1.06L8 9.06l-4.72 4.72a.75.75 0 0 1-1.06-1.06L6.94 8 2.22 3.28a.75.75 0 0 1 0-1.06Z" />
                  </svg>
                </button>
              </div>
              {/* Status select */}
              <div className="flex items-center gap-2">
                <label
                  htmlFor={`ac-status-${idx}`}
                  className="text-xs text-zinc-500 dark:text-zinc-400"
                >
                  Status
                </label>
                <select
                  id={`ac-status-${idx}`}
                  value={item.status}
                  onChange={(e) =>
                    updateItemStatus(idx, e.target.value as AcceptanceCriterion["status"])
                  }
                  disabled={saving || disabled}
                  data-ac-status-select={idx}
                  className="rounded border border-zinc-300 bg-white px-2 py-0.5 text-xs text-zinc-800 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
                >
                  {STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                {item.verified_by && (
                  <span className="text-xs text-zinc-400 dark:text-zinc-500">
                    by {item.verified_by}
                  </span>
                )}
              </div>
            </li>
          ))}
        </ol>

        {/* Add item button */}
        <button
          type="button"
          onClick={addItem}
          disabled={saving || disabled}
          aria-label="Add acceptance criterion"
          data-ac-add-item
          className="self-start rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-800 disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-600 dark:hover:text-zinc-200"
        >
          + Add criterion
        </button>

        {/* Save / Cancel */}
        <div className="flex gap-2 border-t border-zinc-200 pt-2 dark:border-zinc-700">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || disabled}
            data-ac-save
            className="rounded border border-violet-300 bg-violet-600 px-3 py-1 text-xs font-semibold text-white hover:bg-violet-700 disabled:opacity-50 dark:border-violet-700 dark:bg-violet-700 dark:hover:bg-violet-600"
          >
            {saving ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={handleCancel}
            disabled={saving}
            data-ac-cancel
            className="rounded border border-zinc-200 bg-white px-3 py-1 text-xs font-medium text-zinc-700 hover:border-zinc-300 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300"
          >
            Cancel
          </button>
        </div>
      </div>
    </section>
  );
}
