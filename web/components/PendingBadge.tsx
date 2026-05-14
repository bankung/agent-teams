import type { TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { Icon } from "./Icon";

type Props = { task: TaskRead };

export function PendingBadge({ task }: Props) {
  if (!task.is_pending || task.process_status !== TaskStatus.IN_PROGRESS) {
    return null;
  }
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium text-yellow-700 bg-yellow-50 dark:text-yellow-300 dark:bg-yellow-900/30">
      <Icon name="status-queued" size={11} />
      pending
    </span>
  );
}
