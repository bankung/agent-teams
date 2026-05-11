// Server-rendered banner. Grant action is a Client child (composition pattern):
// banner stays SSR; only ProjectConsentGrantModal is "use client". V3 #407.
import type { ProjectRead } from "@/lib/api";
import { ProjectConsentGrantModal } from "@/components/ProjectConsentGrantModal";

type Props = {
  project: ProjectRead;
  hasHeadlessTask?: boolean;
};

export function ProjectConsentBanner({ project, hasHeadlessTask = false }: Props) {
  const consentedAt = project.auto_run_consent_at;

  if (consentedAt === null) {
    // Default — safe state, low urgency.
    return (
      <div className="flex items-center rounded border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-600">
        <span>
          Headless auto-run not enabled for this project.
          {hasHeadlessTask && (
            <span className="ml-2 text-amber-700">
              ⚠ A task in this project is marked auto-headless but consent is not granted — those tasks cannot run.
            </span>
          )}
        </span>
        <ProjectConsentGrantModal
          project={{ id: project.id, name: project.name }}
        />
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
