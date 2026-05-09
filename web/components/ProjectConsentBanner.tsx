// Read-only consent state surface (#481-C / #484). Grant/revoke flow is V3+.
// V2 (#406) will derive hasHeadlessTask from the task list.
import type { ProjectRead } from "@/lib/api";

type Props = {
  project: ProjectRead;
  hasHeadlessTask?: boolean;
};

export function ProjectConsentBanner({ project, hasHeadlessTask = false }: Props) {
  const consentedAt = project.auto_run_consent_at;

  if (consentedAt === null) {
    // Default — safe state, low urgency.
    return (
      <div className="rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-600">
        Headless auto-run not enabled for this project.
        {hasHeadlessTask && (
          <span className="ml-2 text-amber-700">
            ⚠ A task in this project is marked auto-headless but consent is not granted — those tasks cannot run.
          </span>
        )}
      </div>
    );
  }

  // Format ISO 8601 → YYYY-MM-DD for display.
  const consentedDay = consentedAt.slice(0, 10);

  return (
    <div className="rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
      Auto-headless consented {consentedDay}.
      {hasHeadlessTask && <span className="ml-2 font-medium">⚠ active</span>}
    </div>
  );
}
