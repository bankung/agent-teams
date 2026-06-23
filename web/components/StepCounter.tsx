type Props = { done: number; total: number };

export function StepCounter({ done, total }: Props) {
  const complete = total > 0 && done === total;
  const cls = complete
    ? "text-emerald-700 bg-emerald-50 dark:text-emerald-300 dark:bg-emerald-900/30"
    : "text-zinc-600 bg-zinc-100 dark:text-zinc-300 dark:bg-zinc-800";
  return (
    <span
      data-step-counter
      data-step-done={done}
      data-step-total={total}
      title={`${done} of ${total} checklist steps done`}
      className={`glass-pill inline-flex items-center rounded px-1.5 py-0.5 font-mono text-[11px] font-medium tabular-nums ${cls}`}
    >
      {done}/{total}
    </span>
  );
}
