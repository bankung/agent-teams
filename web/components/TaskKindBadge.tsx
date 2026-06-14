import type { TaskKindValue } from "@/lib/constants";
import { Icon } from "./Icon";

type Props = { kind: TaskKindValue };

export function TaskKindBadge({ kind }: Props) {
  if (kind === "ai") return null;
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
