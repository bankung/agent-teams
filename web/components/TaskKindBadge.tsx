import type { TaskKindValue } from "@/lib/constants";

type Props = { kind: TaskKindValue };

export function TaskKindBadge({ kind }: Props) {
  if (kind === "human") {
    return (
      <span
        aria-label="human"
        title="human"
        className="inline-flex items-center text-zinc-600 dark:text-zinc-400"
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
          <circle cx="8" cy="5" r="2.5" />
          <path d="M3 14c0-2.5 2.5-4.5 5-4.5s5 2 5 4.5" />
        </svg>
      </span>
    );
  }
  return (
    <span
      aria-label="ai"
      title="ai"
      className="inline-flex items-center rounded px-1 py-0.5 text-violet-700 bg-violet-50 dark:text-violet-300 dark:bg-violet-900/30"
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
        <rect x="3" y="5" width="10" height="8" rx="1.5" />
        <path d="M8 5V3" />
        <circle cx="8" cy="2.5" r="0.5" fill="currentColor" />
        <circle cx="6" cy="9" r="0.7" fill="currentColor" />
        <circle cx="10" cy="9" r="0.7" fill="currentColor" />
      </svg>
    </span>
  );
}
