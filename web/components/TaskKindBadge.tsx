import type { TaskKindValue } from "@/lib/constants";
import { Icon } from "./Icon";

type Props = { kind: TaskKindValue };

export function TaskKindBadge({ kind }: Props) {
  if (kind === "human") {
    return (
      <span
        aria-label="human"
        title="human"
        className="inline-flex items-center text-zinc-600 dark:text-zinc-400"
      >
        <Icon name="human-agent" size={14} />
      </span>
    );
  }
  return (
    <span
      aria-label="ai"
      title="ai"
      className="inline-flex items-center rounded px-1 py-0.5 text-violet-700 bg-violet-50 dark:text-violet-300 dark:bg-violet-900/30"
    >
      <Icon name="ai-agent" size={14} />
    </span>
  );
}
