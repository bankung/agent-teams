// Tailwind tokens chosen so V2 (#406) visual pass can swap centrally:
//   manual=text-zinc-500 (low weight), auto_pickup=blue-50/700, auto_headless=amber-50/700.
import type { TaskRunModeValue } from "@/lib/constants";

type Props = { mode: TaskRunModeValue };

export function RunModeBadge({ mode }: Props) {
  if (mode === "manual") {
    return <span className="text-xs text-zinc-500">manual</span>;
  }
  if (mode === "auto_pickup") {
    return (
      <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium text-blue-700 bg-blue-50">
        auto A2
      </span>
    );
  }
  // auto_headless — high visual weight (destructive-surface mode).
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium text-amber-700 bg-amber-50"
      title="Headless run — no per-Write approval prompts"
    >
      auto B ⚠
    </span>
  );
}
