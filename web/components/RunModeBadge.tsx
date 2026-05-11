import type { TaskRunModeValue } from "@/lib/constants";

type Props = { mode: TaskRunModeValue };

export function RunModeBadge({ mode }: Props) {
  if (mode === "manual") {
    return (
      <span
        aria-label="manual"
        title="manual"
        className="inline-flex items-center text-zinc-600"
      >
        <svg
          viewBox="0 0 16 16"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M3 12V4l2.5 4L8 4v8" />
          <path d="M10 12V4l2.5 4L15 4v8" />
        </svg>
      </span>
    );
  }
  const label = mode === "auto_pickup" ? "auto pickup" : "auto headless";
  const title =
    mode === "auto_pickup"
      ? "auto pickup"
      : "auto headless — no per-Write approval prompts";
  return (
    <span
      aria-label={label}
      title={title}
      className="inline-flex items-center rounded px-1 py-0.5 text-emerald-700 bg-emerald-50"
    >
      <svg
        viewBox="0 0 16 16"
        width="14"
        height="14"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M13 8a5 5 0 1 1-1.5-3.5" />
        <path d="M13 2v3h-3" />
        <path d="M6 11l2-5 2 5" />
        <path d="M6.7 9.5h2.6" />
      </svg>
    </span>
  );
}
