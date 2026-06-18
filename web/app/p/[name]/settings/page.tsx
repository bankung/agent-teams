// Per-project settings — Kanban #1349, consolidated #2380 (R-merge).
//
// The per-project settings surface was merged into the global /settings page
// (project-scoped via ?project=). This route is now a permanent redirect so
// old links / bookmarks keep working.

import { redirect } from "next/navigation";

type Props = { params: Promise<{ name: string }> };

export const dynamic = "force-dynamic";

export default async function ProjectSettingsRedirect(props: Props) {
  const params = await props.params;
  redirect(`/settings?project=${encodeURIComponent(params.name)}`);
}
