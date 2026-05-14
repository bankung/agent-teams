import type { TaskRead } from "@/lib/api";
import { Icon } from "./Icon";

type Props = { task: TaskRead };

export function RecurrenceIndicator({ task }: Props) {
  // Self-suppress on the dominant case (38/38 today) — render nothing rather than
  // an empty <span> so the parent's mt-2 on the next row handles spacing cleanly.
  if (task.is_template) {
    const formatted = task.next_fire_at
      ? new Date(task.next_fire_at).toLocaleString()
      : "(pending)";
    const title =
      task.next_fire_at !== null
        ? `next fire: ${formatted} (${task.recurrence_timezone})`
        : "next fire: (pending)";
    return (
      <div className="mt-1 inline-flex items-center gap-1 text-[11px] text-zinc-500 dark:text-zinc-400" title={title}>
        <Icon name="sprint" size={11} />
        {task.recurrence_rule}
      </div>
    );
  }
  if (task.spawned_from_task_id !== null) {
    return (
      <div className="mt-1 inline-flex items-center gap-1 text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
        <Icon name="spawn" size={11} />
        from #{task.spawned_from_task_id}
      </div>
    );
  }
  return null;
}
