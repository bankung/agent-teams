// Settings categories — single source of truth for the two-pane settings layout
// (Kanban #2716). The settings page (Server Component) and SettingsNav (client)
// both derive their category list + active-section resolution from here so a
// future service-toggled category can be appended in ONE place without
// restructuring either consumer.
//
// Scope model:
//   - "global"  categories always render (Appearance, Notifications,
//     Integrations, Tour & help).
//   - "project" categories render only when ?project= resolves to a real
//     project (Project, Advanced).

export type SettingsSectionId =
  | "appearance"
  | "notifications"
  | "integrations"
  | "tour"
  | "project"
  | "advanced";

export type SettingsCategoryScope = "global" | "project";

export type SettingsCategory = {
  id: SettingsSectionId;
  label: string;
  scope: SettingsCategoryScope;
};

// Default section when ?section= is absent / unknown / a project-only section is
// requested without a resolved project.
export const DEFAULT_SETTINGS_SECTION: SettingsSectionId = "appearance";

// Ordered category list. Global first (easy → broad), project-scoped last.
const CATEGORIES: readonly SettingsCategory[] = [
  { id: "appearance", label: "Appearance", scope: "global" },
  { id: "notifications", label: "Notifications", scope: "global" },
  { id: "integrations", label: "Integrations", scope: "global" },
  { id: "tour", label: "Tour & help", scope: "global" },
  { id: "project", label: "Project", scope: "project" },
  { id: "advanced", label: "Advanced", scope: "project" },
];

// Visible categories for the current scope: all globals, plus project-scoped
// ones only when a project is in scope.
export function getSettingsCategories(hasProject: boolean): SettingsCategory[] {
  return CATEGORIES.filter((c) => c.scope === "global" || hasProject);
}

// Resolve the requested section to a renderable one. Falls back to the default
// (Appearance) when the id is unknown, or when a project-scoped section is
// requested without a resolved project — so the right pane never renders empty
// or crashes.
export function resolveSettingsSection(
  requested: string | undefined,
  hasProject: boolean,
): SettingsSectionId {
  const visible = getSettingsCategories(hasProject);
  const match = visible.find((c) => c.id === requested);
  return match ? match.id : DEFAULT_SETTINGS_SECTION;
}

// Whether a section needs the audit-task fetch (listAllTasks). Only Advanced
// renders AuditHistorySection, so every other section skips the fetch for a
// lighter payload. ApprovalPoliciesEditor (also under Advanced) fetches its own
// closed-task stats client-side, so the server fetch is purely for audit rows.
export function sectionNeedsAudit(section: SettingsSectionId): boolean {
  return section === "advanced";
}
