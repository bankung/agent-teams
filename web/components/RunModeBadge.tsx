import type { TaskRunModeValue } from "@/lib/constants";
import { Icon } from "./Icon";

type Props = { mode: TaskRunModeValue };

export function RunModeBadge({ mode }: Props) {
  if (mode === "manual") return null;
  const label = mode === "auto_pickup" ? "auto pickup" : "auto headless";
  const title =
    mode === "auto_pickup"
      ? "auto pickup"
      : "auto headless — no per-Write approval prompts";
  return (
    <span
      aria-label={label}
      title={title}
      className="glass-pill inline-flex items-center rounded px-1 py-0.5 text-emerald-700 bg-emerald-50 dark:text-emerald-300 dark:bg-emerald-900/30"
    >
      <Icon name="auto-run" size={14} />
    </span>
  );
}
