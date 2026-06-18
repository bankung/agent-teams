"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";

import { patchTask, type MilestoneRead, type TaskRead } from "@/lib/api";
import { TaskStatus, type TaskStatusValue } from "@/lib/constants";
import { extractErrorMessage } from "@/lib/errors";
import {
  buildMonthGrid,
  buildWeekDays,
  monthLabel,
  monthParamKey,
  weekLabel,
  addMonths,
  addWeeks,
  epochDay,
  normalizeDateOnly,
  startOfWeekKey,
  todayKey,
  currentYearMonth,
  WEEKDAY_LABELS,
  type YearMonth,
} from "@/lib/calendarDates";
import { NewTaskModal } from "./NewTaskModal";
import { CalendarTaskPicker } from "./CalendarTaskPicker";

// CalendarView — month/week task calendar (#1873 M2 + Wave E #11/#12/#13/#14).
//
// Server component (page.tsx) resolves the project + visible month from the
// `?month=YYYY-MM` URL param and SSR-fetches the project's tasks + milestones.
// This client view owns:
//   - the Sun-started month grid (5 or 6 rows; pad days dimmed) AND a 7-day
//     week strip, switchable via a Month | Week toggle (#13).
//   - Prev / Next / Today nav. Month mode rewrites ?month (server re-fetch);
//     week mode keeps a local anchor (the fetched task set covers a superset).
//   - placement (#14): UNFINISHED tasks (process_status NOT in {DONE,CANCELLED})
//     land on due_date; FINISHED tasks (DONE) land on completed_at. A finished
//     task whose completion date is LATER than its due_date renders light-red
//     (completed-late). CANCELLED + tasks with no relevant date are dropped.
//   - per-cell "+N more" overflow (month: 2 chips; week: fuller list).
//   - right-click a day cell → context menu (#11): "New task on this date"
//     (pre-fills NewTaskModal due_date) + "Add existing task" (searchable picker
//     → PATCH that task's due_date).
//   - drag a task chip onto another day (#12): PATCH due_date = target day,
//     optimistic + revert (mirrors MilestonesView dnd-kit pattern).
//
// NOTE on fetch (page.tsx): finished tasks place by completed_at, which the BE
// has no range filter for (only due_from/due_to). So page.tsx fetches the
// project's task set (no due range) and this view filters placement client-side.
// Acceptable for a single-project calendar; a BE completed_* filter is a future
// follow-up if volume grows.

// Per-process_status chip color — mirrors the board lane vocabulary.
const STATUS_CHIP: Record<TaskStatusValue, string> = {
  [TaskStatus.TODO]:
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  [TaskStatus.IN_PROGRESS]:
    "bg-amber-50 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200",
  [TaskStatus.REVIEW]:
    "bg-sky-50 text-sky-800 dark:bg-sky-900/30 dark:text-sky-200",
  [TaskStatus.BLOCKED]:
    "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200",
  [TaskStatus.DONE]:
    "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-200",
  [TaskStatus.CANCELLED]:
    "bg-zinc-100 text-zinc-400 line-through dark:bg-zinc-800 dark:text-zinc-500",
  [TaskStatus.HALTED_PENDING_USER]:
    "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
};

// #14 — completed-late chip: a DONE task whose completed_at (date) is later than
// its due_date. Light-red background distinct from the BLOCKED red chip.
const COMPLETED_LATE_CHIP =
  "bg-rose-100 text-rose-800 dark:bg-rose-950/50 dark:text-rose-200";

const STATUS_LABEL: Record<TaskStatusValue, string> = {
  [TaskStatus.TODO]: "todo",
  [TaskStatus.IN_PROGRESS]: "in progress",
  [TaskStatus.REVIEW]: "review",
  [TaskStatus.BLOCKED]: "blocked",
  [TaskStatus.DONE]: "done",
  [TaskStatus.CANCELLED]: "cancelled",
  [TaskStatus.HALTED_PENDING_USER]: "halted",
};

// Month mode: keep cells compact (≤2 chips + overflow). Week mode: a fuller
// per-day list (the strip is taller).
const MAX_MONTH_CHIPS = 2;
const MAX_WEEK_CHIPS = 12;

type CalMode = "month" | "week";

// PlacedTask — a task resolved to a placement cell key, with the #14 late flag.
type PlacedTask = {
  task: TaskRead;
  key: string; // placement cell "YYYY-MM-DD"
  late: boolean; // finished AND completed_at date > due_date
};

// resolvePlacement — #14 placement rule. Returns null for tasks with no relevant
// date (CANCELLED, unfinished-without-due, finished-without-completed_at) — those
// fall to the existing "unscheduled" handling (dropped from the grid).
function resolvePlacement(task: TaskRead): PlacedTask | null {
  if (task.process_status === TaskStatus.CANCELLED) return null;

  if (task.process_status === TaskStatus.DONE) {
    // Finished → place on the completion date (fallback to due_date if the row
    // somehow lacks completed_at, e.g. legacy/imported rows).
    const completedKey = normalizeDateOnly(task.completed_at);
    const dueKey = normalizeDateOnly(task.due_date);
    const key = completedKey ?? dueKey;
    if (!key) return null;
    // completed-late: both dates present AND completion day strictly after due.
    let late = false;
    if (completedKey && dueKey) {
      const c = epochDay(completedKey);
      const d = epochDay(dueKey);
      if (c !== null && d !== null) late = c > d;
    }
    return { task, key, late };
  }

  // Unfinished (TODO / IN_PROGRESS / REVIEW / BLOCKED) → place on due_date.
  const dueKey = normalizeDateOnly(task.due_date);
  if (!dueKey) return null;
  return { task, key: dueKey, late: false };
}

// Stable per-cell order: process_status then id (mirrors prior behaviour).
function sortPlaced(a: PlacedTask, b: PlacedTask): number {
  return (
    a.task.process_status - b.task.process_status || a.task.id - b.task.id
  );
}

type ContextMenuState = {
  key: string; // the day cell the menu was opened on
  x: number;
  y: number;
};

type Props = {
  projectId: number;
  projectName: string;
  year: number;
  month0: number; // 0..11
  tasks: TaskRead[];
  milestones: MilestoneRead[];
};

export function CalendarView({
  projectId,
  projectName,
  year,
  month0,
  tasks,
  milestones,
}: Props) {
  const router = useRouter();
  const ym: YearMonth = useMemo(() => ({ year, month0 }), [year, month0]);

  // #13 — view mode (month | week) + week anchor. Mode is local UI state; week
  // anchor seeds from the visible month's 1st so a Month→Week toggle lands on
  // the week containing that date. Month nav rewrites ?month (server re-fetch).
  const [mode, setMode] = useState<CalMode>("month");
  const [weekAnchor, setWeekAnchor] = useState<string>(() =>
    startOfWeekKey(`${year}-${String(month0 + 1).padStart(2, "0")}-01`),
  );

  const today = useMemo(() => todayKey(), []);

  // #12/#11 — optimistic overrides for due_date PATCHes (drag + picker). Keyed
  // by task id → the locally-applied due_date. due_date drives placement for
  // unfinished tasks (DONE tasks place by completed_at, so a drag of a DONE task
  // may not visibly move). An override is held until the server (via
  // router.refresh re-fetch) confirms the same value — then this effect drops it,
  // so there's no flicker back to the old day in the gap before the fetch lands.
  const [dueOverride, setDueOverride] = useState<Record<number, string>>({});

  // Self-heal: once the incoming server `tasks` prop reflects an override's
  // value, drop that override (server is now authoritative). A FAILED PATCH
  // already reverted the override in its catch, so it never reaches here.
  useEffect(() => {
    setDueOverride((prev) => {
      const keys = Object.keys(prev);
      if (keys.length === 0) return prev;
      let changed = false;
      const next = { ...prev };
      for (const t of tasks) {
        const ov = prev[t.id];
        if (ov !== undefined && normalizeDateOnly(t.due_date) === ov) {
          delete next[t.id];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [tasks]);

  // Apply overrides on top of the server task list before placement.
  const effectiveTasks = useMemo(() => {
    if (Object.keys(dueOverride).length === 0) return tasks;
    return tasks.map((t) =>
      dueOverride[t.id] !== undefined
        ? { ...t, due_date: dueOverride[t.id] }
        : t,
    );
  }, [tasks, dueOverride]);

  // #14 — resolve every task to a placement cell (or drop it).
  const placed = useMemo(
    () =>
      effectiveTasks
        .map(resolvePlacement)
        .filter((p): p is PlacedTask => p !== null),
    [effectiveTasks],
  );

  const placedByDay = useMemo(() => {
    const map = new Map<string, PlacedTask[]>();
    for (const p of placed) {
      const bucket = map.get(p.key);
      if (bucket) bucket.push(p);
      else map.set(p.key, [p]);
    }
    for (const bucket of map.values()) bucket.sort(sortPlaced);
    return map;
  }, [placed]);

  // Milestones index (unchanged behaviour — deadline markers by target_date).
  const milestonesByDay = useMemo(() => {
    const map = new Map<string, MilestoneRead[]>();
    for (const m of milestones) {
      const key = normalizeDateOnly(m.target_date);
      if (!key) continue;
      const bucket = map.get(key);
      if (bucket) bucket.push(m);
      else map.set(key, [m]);
    }
    for (const bucket of map.values()) bucket.sort((a, b) => a.id - b.id);
    return map;
  }, [milestones]);

  const boardHref = `/p/${encodeURIComponent(projectName)}`;
  // Wave A.2c — the dedicated /milestones page was removed; the Gantt view is
  // now the milestone home. Milestone deadline chips deep-link there.
  const milestonesHref = `/p/${encodeURIComponent(projectName)}/gantt`;
  const calendarHref = `/p/${encodeURIComponent(projectName)}/calendar`;

  const goToMonth = (target: YearMonth) => {
    router.push(`${calendarHref}?month=${monthParamKey(target)}`);
  };

  // ── #11 context menu ───────────────────────────────────────────────────────
  const [menu, setMenu] = useState<ContextMenuState | null>(null);
  // The day a "New task on this date" / picker action targets.
  const [createForDay, setCreateForDay] = useState<string | null>(null);
  const [pickerForDay, setPickerForDay] = useState<string | null>(null);

  const openMenu = useCallback((key: string, x: number, y: number) => {
    setMenu({ key, x, y });
  }, []);
  const closeMenu = useCallback(() => setMenu(null), []);

  // Close the context menu on outside-click / Esc / scroll.
  useEffect(() => {
    if (!menu) return;
    function onDown(e: MouseEvent) {
      const t = e.target as HTMLElement;
      if (t.closest("[data-calendar-context-menu]")) return;
      closeMenu();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeMenu();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", closeMenu, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, [menu, closeMenu]);

  // ── #12 drag-to-reschedule (dnd-kit; mirrors MilestonesView) ───────────────
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );
  const [activeTask, setActiveTask] = useState<TaskRead | null>(null);
  const [dndError, setDndError] = useState<string | null>(null);

  const onDragStart = useCallback((event: DragStartEvent) => {
    const t = event.active.data.current?.task as TaskRead | undefined;
    setActiveTask(t ?? null);
  }, []);

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null);
      const { active, over } = event;
      if (!over) return;

      const task = active.data.current?.task as TaskRead | undefined;
      const sourceKey = active.data.current?.sourceKey as string | undefined;
      if (!task || sourceKey === undefined) return;

      // Drop target ids are namespaced `day-YYYY-MM-DD`.
      const overId = String(over.id);
      if (!overId.startsWith("day-")) return;
      const destKey = overId.slice("day-".length);

      // No-op: dropped back on the same day.
      if (destKey === sourceKey) return;

      const prevDue = task.due_date ?? null;

      // Optimistic: apply the new due_date locally (drives placement).
      setDueOverride((prev) => ({ ...prev, [task.id]: destKey }));

      patchTask(projectId, task.id, { due_date: destKey })
        .then(() => {
          // Server is authoritative — refresh re-fetches; the reconcile effect
          // drops the override once the new due_date lands (no flicker).
          router.refresh();
        })
        .catch((err: unknown) => {
          // Revert: restore the prior due_date (or remove the override).
          setDueOverride((prev) => {
            const next = { ...prev };
            if (prevDue === null) delete next[task.id];
            else next[task.id] = prevDue;
            return next;
          });
          setDndError(
            `Task #${task.id}: ${extractErrorMessage(err, "Reschedule failed")}`,
          );
        });
    },
    [projectId, router],
  );

  // #11 picker commit — PATCH the chosen task's due_date to the target day.
  const onPickExisting = useCallback(
    (task: TaskRead, dayKey: string) => {
      setPickerForDay(null);
      const prevDue = task.due_date ?? null;
      setDueOverride((prev) => ({ ...prev, [task.id]: dayKey }));
      patchTask(projectId, task.id, { due_date: dayKey })
        .then(() => {
          // Reconcile effect drops the override when the refetch confirms it.
          router.refresh();
        })
        .catch((err: unknown) => {
          setDueOverride((prev) => {
            const next = { ...prev };
            if (prevDue === null) delete next[task.id];
            else next[task.id] = prevDue;
            return next;
          });
          setDndError(
            `Task #${task.id}: ${extractErrorMessage(err, "Add to date failed")}`,
          );
        });
    },
    [projectId, router],
  );

  const totalPlaced = placed.length;
  const totalMilestones = milestones.filter((m) =>
    normalizeDateOnly(m.target_date),
  ).length;
  const totalItems = totalPlaced + totalMilestones;

  // Nav handlers branch on mode.
  const onPrev = () =>
    mode === "month"
      ? goToMonth(addMonths(ym, -1))
      : setWeekAnchor((a) => addWeeks(a, -1));
  const onNext = () =>
    mode === "month"
      ? goToMonth(addMonths(ym, 1))
      : setWeekAnchor((a) => addWeeks(a, 1));
  const onToday = () =>
    mode === "month"
      ? goToMonth(currentYearMonth())
      : setWeekAnchor(startOfWeekKey(today));

  const headingLabel = mode === "month" ? monthLabel(ym) : weekLabel(weekAnchor);

  return (
    <DndContext
      sensors={sensors}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={() => setActiveTask(null)}
    >
      <section data-calendar-view aria-label={`Calendar for ${projectName}`}>
        {/* Heading + Month|Week toggle + Prev / Today / Next nav. */}
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
              data-calendar-month-label
            >
              {headingLabel}
            </h2>
          </div>
          <div className="flex items-center gap-1.5">
            {/* #13 — Month | Week segmented toggle. */}
            <div
              role="tablist"
              aria-label="Calendar range"
              className="inline-flex items-center overflow-hidden rounded-md border border-zinc-200 text-xs dark:border-zinc-700"
              data-calendar-mode-toggle
            >
              {(["month", "week"] as const).map((m) => {
                const isActive = mode === m;
                return (
                  <button
                    key={m}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    onClick={() => {
                      if (m === "week" && mode === "month") {
                        // Seed the week anchor from the visible month's 1st.
                        setWeekAnchor(
                          startOfWeekKey(
                            `${ym.year}-${String(ym.month0 + 1).padStart(2, "0")}-01`,
                          ),
                        );
                      }
                      setMode(m);
                    }}
                    className={`px-3 py-2 min-h-[44px] capitalize sm:min-h-0 sm:px-2.5 sm:py-1 transition-colors ${
                      isActive
                        ? "bg-zinc-900 font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900"
                        : "bg-transparent text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
                    }`}
                    data-calendar-mode={m}
                    data-active={isActive ? "true" : undefined}
                  >
                    {m}
                  </button>
                );
              })}
            </div>

            <button
              type="button"
              onClick={onPrev}
              className="glass-glow rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
              aria-label={mode === "month" ? "Previous month" : "Previous week"}
              data-calendar-prev
            >
              ← Prev
            </button>
            <button
              type="button"
              onClick={onToday}
              className="glass-glow rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
              data-calendar-today
            >
              Today
            </button>
            <button
              type="button"
              onClick={onNext}
              className="glass-glow rounded border border-zinc-200 bg-white px-3 py-2 text-xs font-medium text-zinc-700 hover:border-zinc-300 hover:text-zinc-900 min-h-[44px] sm:min-h-0 sm:px-2.5 sm:py-1 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
              aria-label={mode === "month" ? "Next month" : "Next week"}
              data-calendar-next
            >
              Next →
            </button>
          </div>
        </div>

        {/* #12 — inline DnD/PATCH failure notice (revert already happened). */}
        {dndError !== null && (
          <p
            className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300"
            role="alert"
            data-calendar-dnd-error
          >
            {dndError}
            <button
              type="button"
              onClick={() => setDndError(null)}
              className="ml-2 underline hover:no-underline"
            >
              dismiss
            </button>
          </p>
        )}

        {totalItems === 0 && (
          <p
            className="mb-3 rounded border border-dashed border-zinc-200 px-4 py-3 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400"
            data-calendar-empty
          >
            No tasks or milestone deadlines in {headingLabel}.
          </p>
        )}

        {mode === "month" ? (
          <MonthGrid
            ym={ym}
            today={today}
            placedByDay={placedByDay}
            milestonesByDay={milestonesByDay}
            boardHref={boardHref}
            milestonesHref={milestonesHref}
            onContextMenu={openMenu}
            onNewTask={(key) => setCreateForDay(key)}
          />
        ) : (
          <WeekStrip
            anchor={weekAnchor}
            today={today}
            placedByDay={placedByDay}
            milestonesByDay={milestonesByDay}
            boardHref={boardHref}
            milestonesHref={milestonesHref}
            onContextMenu={openMenu}
            onNewTask={(key) => setCreateForDay(key)}
          />
        )}

        {/* #11 — day-cell context menu (portal-free; absolutely positioned). */}
        {menu && (
          <DayContextMenu
            state={menu}
            onNewTask={() => {
              setCreateForDay(menu.key);
              closeMenu();
            }}
            onAddExisting={() => {
              setPickerForDay(menu.key);
              closeMenu();
            }}
            onClose={closeMenu}
          />
        )}

        {/* DragOverlay — floating preview of the dragged task chip (#12). */}
        <DragOverlay dropAnimation={null}>
          {activeTask ? (
            <div className="pointer-events-none rounded border border-zinc-300 bg-white px-2 py-1 text-xs shadow-lg dark:border-zinc-600 dark:bg-zinc-800">
              <span className="text-zinc-800 dark:text-zinc-200">
                {activeTask.title}
              </span>
            </div>
          ) : null}
        </DragOverlay>

        {/* #11 — "New task on this date": NewTaskModal pre-filled with due_date.
            Keyed on the day so the modal re-seeds per target date. */}
        {createForDay !== null && (
          <NewTaskModal
            key={`new-${createForDay}`}
            projectId={projectId}
            externalOpen
            onExternalClose={() => setCreateForDay(null)}
            initialDueDate={createForDay}
          />
        )}

        {/* #11 — "Add existing task to this date": searchable picker → PATCH. */}
        {pickerForDay !== null && (
          <CalendarTaskPicker
            projectId={projectId}
            dayKey={pickerForDay}
            onPick={(task) => onPickExisting(task, pickerForDay)}
            onClose={() => setPickerForDay(null)}
          />
        )}
      </section>
    </DndContext>
  );
}

// ── #11 day-cell context menu ───────────────────────────────────────────────
function DayContextMenu({
  state,
  onNewTask,
  onAddExisting,
  onClose,
}: {
  state: ContextMenuState;
  onNewTask: () => void;
  onAddExisting: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // Clamp the menu inside the viewport (a right/bottom-edge right-click would
  // otherwise render it partly off-screen), then focus the first item.
  const [pos, setPos] = useState({ top: state.y, left: state.x });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const pad = 8;
    const left = Math.min(state.x, window.innerWidth - r.width - pad);
    const top = Math.min(state.y, window.innerHeight - r.height - pad);
    setPos({ top: Math.max(pad, top), left: Math.max(pad, left) });
    el.querySelector<HTMLButtonElement>("button")?.focus();
  }, [state.x, state.y]);

  // Arrow-key roving focus between the two items.
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      ref.current?.querySelectorAll<HTMLButtonElement>("button") ?? [],
    );
    const idx = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(idx + 1) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Tab") {
      onClose();
    }
  };

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for ${state.key}`}
      data-calendar-context-menu
      data-calendar-context-day={state.key}
      onKeyDown={onKeyDown}
      style={{ top: pos.top, left: pos.left }}
      className="fixed z-50 min-w-[14rem] rounded-md border border-zinc-200 bg-white py-1 text-sm shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
    >
      <div className="border-b border-zinc-100 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-400 dark:border-zinc-800 dark:text-zinc-500">
        {state.key}
      </div>
      <button
        type="button"
        role="menuitem"
        onClick={onNewTask}
        className="block w-full px-3 py-2 text-left text-zinc-700 hover:bg-zinc-100 focus:bg-zinc-100 focus:outline-none dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800"
        data-calendar-menu-new-task
      >
        New task on this date
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={onAddExisting}
        className="block w-full px-3 py-2 text-left text-zinc-700 hover:bg-zinc-100 focus:bg-zinc-100 focus:outline-none dark:text-zinc-200 dark:hover:bg-zinc-800 dark:focus:bg-zinc-800"
        data-calendar-menu-add-existing
      >
        Add existing task to this date
      </button>
    </div>
  );
}

// ── Shared chip + cell building blocks ──────────────────────────────────────

// TaskChip — a draggable task chip (#12). Right-click bubbles to the day cell
// (the cell owns the context menu), so we don't stop propagation here.
function TaskChip({
  placed,
  sourceKey,
  boardHref,
}: {
  placed: PlacedTask;
  sourceKey: string;
  boardHref: string;
}) {
  const { task, late } = placed;
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `cal-task-${task.id}`,
    data: { task, sourceKey },
  });

  const chipColor = late
    ? COMPLETED_LATE_CHIP
    : STATUS_CHIP[task.process_status] ?? STATUS_CHIP[TaskStatus.TODO];

  const statusLabel = STATUS_LABEL[task.process_status] ?? "task";
  const title = late
    ? `#${task.id} ${task.title} (done late — due ${normalizeDateOnly(task.due_date)}, completed ${normalizeDateOnly(task.completed_at)})`
    : `#${task.id} ${task.title} (${statusLabel})`;

  return (
    <div
      ref={setNodeRef}
      data-calendar-task={task.id}
      data-calendar-task-late={late ? "true" : undefined}
      className={`flex items-center gap-1 rounded text-[11px] ${chipColor} ${
        isDragging ? "opacity-40" : ""
      }`}
    >
      {/* Drag handle carries the dnd listeners so the chip link stays clickable.
          Focusable (no tabIndex=-1) so the KeyboardSensor can start a keyboard
          drag — mirrors MilestonesView's handle. */}
      <button
        type="button"
        {...attributes}
        {...listeners}
        aria-label={`Drag task #${task.id} to reschedule`}
        className="shrink-0 cursor-grab touch-none rounded px-0.5 leading-none opacity-60 hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-blue-400 active:cursor-grabbing"
        data-calendar-task-handle
      >
        ⠿
      </button>
      <Link
        href={`${boardHref}?task=${task.id}`}
        title={title}
        className="min-w-0 flex-1 truncate py-0.5 pr-1 hover:opacity-80"
      >
        {task.title}
      </Link>
    </div>
  );
}

// MilestoneChip — deadline marker (unchanged vocabulary).
function MilestoneChip({
  milestone,
  milestonesHref,
}: {
  milestone: MilestoneRead;
  milestonesHref: string;
}) {
  return (
    <Link
      href={milestonesHref}
      title={`Milestone deadline: ${milestone.title}`}
      data-calendar-milestone={milestone.id}
      className="flex items-center gap-1 truncate rounded bg-violet-50 px-1 py-0.5 text-[11px] font-medium text-violet-800 hover:bg-violet-100 dark:bg-violet-900/30 dark:text-violet-200 dark:hover:bg-violet-900/50"
    >
      <span aria-hidden>🎯</span>
      <span className="truncate">{milestone.title}</span>
    </Link>
  );
}

// DroppableDay — a day cell that accepts task-chip drops (#12) + opens the
// context menu on right-click (#11). Shared by month + week renderers.
function DroppableDay({
  dayKey,
  isToday,
  inMonth,
  className,
  children,
  onContextMenu,
  onNewTask,
}: {
  dayKey: string;
  isToday: boolean;
  inMonth: boolean;
  className: string;
  children: React.ReactNode;
  onContextMenu: (key: string, x: number, y: number) => void;
  onNewTask: (key: string) => void;
}) {
  const { isOver, setNodeRef } = useDroppable({ id: `day-${dayKey}` });
  const dropHighlight = isOver
    ? " ring-2 ring-inset ring-blue-400/70 dark:ring-blue-500/70"
    : "";

  return (
    <div
      ref={setNodeRef}
      data-calendar-cell={dayKey}
      data-cell-in-month={inMonth ? "true" : "false"}
      data-cell-today={isToday ? "true" : undefined}
      data-drop-over={isOver || undefined}
      onContextMenu={(e) => {
        e.preventDefault();
        onContextMenu(dayKey, e.clientX, e.clientY);
      }}
      className={`relative group ${className}${dropHighlight}`}
    >
      {children}
      {inMonth && (
        <button
          type="button"
          data-calendar-new-task={dayKey}
          aria-label={`New task on ${dayKey}`}
          onClick={(e) => {
            e.stopPropagation();
            onNewTask(dayKey);
          }}
          className="absolute top-1 right-1 flex h-5 w-5 min-h-[44px] min-w-[44px] sm:min-h-0 sm:min-w-0 sm:h-5 sm:w-5 items-center justify-center rounded text-zinc-400 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200 dark:text-zinc-500 transition-opacity"
        >
          <span aria-hidden className="text-sm leading-none">+</span>
        </button>
      )}
    </div>
  );
}

// ── Month grid renderer ─────────────────────────────────────────────────────
function MonthGrid({
  ym,
  today,
  placedByDay,
  milestonesByDay,
  boardHref,
  milestonesHref,
  onContextMenu,
  onNewTask,
}: {
  ym: YearMonth;
  today: string;
  placedByDay: Map<string, PlacedTask[]>;
  milestonesByDay: Map<string, MilestoneRead[]>;
  boardHref: string;
  milestonesHref: string;
  onContextMenu: (key: string, x: number, y: number) => void;
  onNewTask: (key: string) => void;
}) {
  const grid = useMemo(() => buildMonthGrid(ym), [ym]);

  return (
    <>
      <div className="glass-surface grid grid-cols-7 gap-px overflow-hidden rounded-t-lg border border-b-0 border-zinc-200 bg-zinc-200 text-center text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-800 dark:text-zinc-400">
        {WEEKDAY_LABELS.map((d) => (
          <div key={d} className="bg-zinc-50 py-1.5 dark:bg-zinc-900">
            {d}
          </div>
        ))}
      </div>

      <div
        className="glass-surface grid grid-cols-7 gap-px overflow-hidden rounded-b-lg border border-zinc-200 bg-zinc-200 dark:border-zinc-800 dark:bg-zinc-800"
        data-calendar-grid
      >
        {grid.flat().map((cell) => {
          const dayPlaced = cell.inMonth ? placedByDay.get(cell.key) ?? [] : [];
          const dayMilestones = cell.inMonth
            ? milestonesByDay.get(cell.key) ?? []
            : [];
          const isToday = cell.key === today;

          // Milestones render first (higher salience); overflow cap is combined.
          const totalCount = dayMilestones.length + dayPlaced.length;
          const milestoneShown = dayMilestones.slice(0, MAX_MONTH_CHIPS);
          const taskBudget = Math.max(
            0,
            MAX_MONTH_CHIPS - milestoneShown.length,
          );
          const tasksShown = dayPlaced.slice(0, taskBudget);
          const overflow =
            totalCount - milestoneShown.length - tasksShown.length;

          return (
            <DroppableDay
              key={cell.key}
              dayKey={cell.key}
              isToday={isToday}
              inMonth={cell.inMonth}
              onContextMenu={onContextMenu}
              onNewTask={onNewTask}
              className={`flex min-h-[92px] flex-col gap-1 p-1.5 ${
                cell.inMonth
                  ? "bg-white dark:bg-zinc-950"
                  : "bg-zinc-50 dark:bg-zinc-900/40"
              } ${isToday ? "ring-2 ring-inset ring-sky-500 dark:ring-sky-400" : ""}`}
            >
              <div className="flex items-center justify-between">
                <span
                  className={`text-xs tabular-nums ${
                    isToday
                      ? "flex h-5 w-5 items-center justify-center rounded-full bg-sky-600 font-semibold text-white dark:bg-sky-500"
                      : cell.inMonth
                        ? "text-zinc-600 dark:text-zinc-300"
                        : "text-zinc-300 dark:text-zinc-600"
                  }`}
                >
                  {cell.day}
                </span>
              </div>

              {milestoneShown.map((m) => (
                <MilestoneChip
                  key={`m-${m.id}`}
                  milestone={m}
                  milestonesHref={milestonesHref}
                />
              ))}
              {tasksShown.map((p) => (
                <TaskChip
                  key={`t-${p.task.id}`}
                  placed={p}
                  sourceKey={cell.key}
                  boardHref={boardHref}
                />
              ))}

              {overflow > 0 && (
                <span
                  className="px-1 text-[10px] font-medium text-zinc-400 dark:text-zinc-500"
                  data-calendar-overflow={overflow}
                >
                  +{overflow} more
                </span>
              )}
            </DroppableDay>
          );
        })}
      </div>
    </>
  );
}

// ── Week strip renderer (#13) ───────────────────────────────────────────────
function WeekStrip({
  anchor,
  today,
  placedByDay,
  milestonesByDay,
  boardHref,
  milestonesHref,
  onContextMenu,
  onNewTask,
}: {
  anchor: string;
  today: string;
  placedByDay: Map<string, PlacedTask[]>;
  milestonesByDay: Map<string, MilestoneRead[]>;
  boardHref: string;
  milestonesHref: string;
  onContextMenu: (key: string, x: number, y: number) => void;
  onNewTask: (key: string) => void;
}) {
  const days = useMemo(() => buildWeekDays(anchor), [anchor]);

  return (
    <div
      className="glass-surface grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-zinc-200 bg-zinc-200 sm:grid-cols-7 dark:border-zinc-800 dark:bg-zinc-800"
      data-calendar-week-grid
    >
      {days.map((d, i) => {
        const dayPlaced = placedByDay.get(d.key) ?? [];
        const dayMilestones = milestonesByDay.get(d.key) ?? [];
        const isToday = d.key === today;
        const tasksShown = dayPlaced.slice(0, MAX_WEEK_CHIPS);
        const overflow = dayPlaced.length - tasksShown.length;

        return (
          <DroppableDay
            key={d.key}
            dayKey={d.key}
            isToday={isToday}
            inMonth
            onContextMenu={onContextMenu}
            onNewTask={onNewTask}
            className={`flex min-h-[7rem] flex-col gap-1 bg-white p-2 sm:min-h-[20rem] dark:bg-zinc-950 ${
              isToday ? "ring-2 ring-inset ring-sky-500 dark:ring-sky-400" : ""
            }`}
          >
            <div className="mb-1 flex items-baseline justify-between border-b border-zinc-100 pb-1 dark:border-zinc-800">
              <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {WEEKDAY_LABELS[i]}
              </span>
              <span
                className={`text-xs tabular-nums ${
                  isToday
                    ? "flex h-5 w-5 items-center justify-center rounded-full bg-sky-600 font-semibold text-white dark:bg-sky-500"
                    : "text-zinc-600 dark:text-zinc-300"
                }`}
              >
                {d.day}
              </span>
            </div>

            {dayMilestones.map((m) => (
              <MilestoneChip
                key={`m-${m.id}`}
                milestone={m}
                milestonesHref={milestonesHref}
              />
            ))}
            {tasksShown.map((p) => (
              <TaskChip
                key={`t-${p.task.id}`}
                placed={p}
                sourceKey={d.key}
                boardHref={boardHref}
              />
            ))}

            {dayMilestones.length === 0 && dayPlaced.length === 0 && (
              <span className="px-1 text-[10px] text-zinc-300 italic dark:text-zinc-600">
                —
              </span>
            )}
            {overflow > 0 && (
              <span
                className="px-1 text-[10px] font-medium text-zinc-400 dark:text-zinc-500"
                data-calendar-overflow={overflow}
              >
                +{overflow} more
              </span>
            )}
          </DroppableDay>
        );
      })}
    </div>
  );
}
