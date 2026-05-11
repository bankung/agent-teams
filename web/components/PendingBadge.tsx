import type { TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";

type Props = { task: TaskRead };

export function PendingBadge({ task }: Props) {
  if (!task.is_pending || task.process_status !== TaskStatus.IN_PROGRESS) {
    return null;
  }
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium text-yellow-700 bg-yellow-50">
      pending
    </span>
  );
}
