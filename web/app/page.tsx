import { redirect } from "next/navigation";

// Root route — the aggregate dashboard IS the landing per Kanban #869.
// Server-side redirect (no flash). The legacy per-project landing via
// NEXT_PUBLIC_PROJECT_NAME has been retired — operators now pick a project
// from the dashboard cards.
export default function Home() {
  redirect("/dashboard");
}
