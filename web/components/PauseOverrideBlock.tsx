"use client";

// PauseOverrideBlock — shared amber "allow during pause" checkbox + reason
// block. Rendered inside NewTaskModal + AiTaskModal when the project is paused.
// Extracted from those modals (Kanban #1682 Phase 1 E3).

import { REASON_MIN_CHARS } from "@/lib/constants";

type Props = {
  allowDuringPause: boolean;
  setAllowDuringPause: (v: boolean) => void;
  allowDuringPauseReason: string;
  setAllowDuringPauseReason: (v: string) => void;
  disabled: boolean;
  onClearError: () => void;
  trimmedOverrideReason: string;
  overrideReasonValid: boolean;
  // data-* prefix for the wrapper + sub-elements, e.g. "new-task" or "ai-task"
  dataPrefix: string;
};

export function PauseOverrideBlock({
  allowDuringPause,
  setAllowDuringPause,
  allowDuringPauseReason,
  setAllowDuringPauseReason,
  disabled,
  onClearError,
  trimmedOverrideReason,
  overrideReasonValid,
  dataPrefix,
}: Props) {
  return (
    <div
      className="mt-3 rounded border border-amber-300 bg-amber-50 px-2 py-1.5 dark:border-amber-600 dark:bg-amber-950/40"
      {...{ [`data-${dataPrefix}-pause-override`]: true }}
    >
      <label className="flex items-start gap-2 text-xs font-medium text-amber-900 dark:text-amber-200">
        <input
          type="checkbox"
          checked={allowDuringPause}
          onChange={(e) => {
            setAllowDuringPause(e.target.checked);
            onClearError();
          }}
          disabled={disabled}
          className="mt-0.5 h-4 w-4 rounded border-amber-400 text-amber-600 focus:ring-amber-500 dark:border-amber-600 dark:bg-zinc-950"
          {...{ [`data-${dataPrefix}-pause-override-toggle`]: true }}
        />
        <span className="flex-1">
          Allow this task during pause{" "}
          <span className="font-normal opacity-80">
            (project is paused — POST will 423 without this)
          </span>
        </span>
      </label>
      {allowDuringPause && (
        <label className="mt-2 block text-xs font-medium text-amber-900 dark:text-amber-200">
          Reason{" "}
          <span className="font-normal opacity-80">
            (≥{REASON_MIN_CHARS} chars)
          </span>{" "}
          <span className="text-red-600 dark:text-red-400">*</span>
          <textarea
            value={allowDuringPauseReason}
            onChange={(e) => {
              setAllowDuringPauseReason(e.target.value);
              onClearError();
            }}
            rows={2}
            placeholder="Why is this task required despite the pause? Captured into projects_audit (action='pause_override')."
            disabled={disabled}
            aria-invalid={
              allowDuringPauseReason.length > 0 && !overrideReasonValid
            }
            className="mt-1 block w-full rounded border border-amber-300 bg-white px-2 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-amber-500 focus:outline-none disabled:opacity-50 dark:border-amber-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            {...{ [`data-${dataPrefix}-pause-override-reason`]: true }}
          />
          <span className="mt-0.5 block text-[10px] tabular-nums opacity-80">
            {trimmedOverrideReason.length}/{REASON_MIN_CHARS}
          </span>
        </label>
      )}
    </div>
  );
}
