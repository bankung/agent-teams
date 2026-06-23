// Help — Kanban #2482. Server Component. Concise how-to guidance for the
// main settings controls so operators understand what each knob does.

import Link from "next/link";

export default function HelpPage() {
  return (
    <main className="glass-board flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950">
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/settings"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Settings
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <Link
          href="/dashboard"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Dashboard
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          How to
        </span>
      </header>

      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8">
        <section
          aria-labelledby="help-heading"
          className="glass-surface flex flex-col gap-6 rounded-md border border-zinc-200 bg-zinc-50/60 p-5 dark:border-zinc-800 dark:bg-zinc-900/40"
        >
          <h1
            id="help-heading"
            className="text-lg font-semibold text-zinc-900 dark:text-zinc-100"
          >
            Settings guide
          </h1>

          <dl className="flex flex-col gap-6">
            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                HITL nudge threshold
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Hours a waiting question/decision task can sit before the
                backend sends a single aging-nudge. Blank or 0 disables nudges
                for the project; per-task override via the task drawer&apos;s
                &ldquo;Mute nudges&rdquo;.
              </dd>
            </div>

            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Approval policies
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Pre-approval rules evaluated before an approval pause reaches
                you. Each policy matches a trigger + conditions and then
                auto-approves, auto-rejects, or routes the decision to you.
                (v0 — stored per project.)
              </dd>
            </div>

            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Integrations
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Connect the project&apos;s external sources so tasks can read
                from them.
              </dd>
            </div>

            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Push notifications
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Opt-in, per-browser web-push for task events (halts, HITL
                prompts). Requires the deploy&apos;s VAPID keys to be
                configured.
              </dd>
            </div>

            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Theme
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Light/Dark, and the Glass/Flat surface style (independent
                axes).
              </dd>
            </div>

            <div>
              <dt className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                Resources
              </dt>
              <dd className="mt-1 text-[13px] text-zinc-600 dark:text-zinc-400 leading-5">
                Files/links attached to the project and referenced by tasks.
              </dd>
            </div>
          </dl>
        </section>
      </div>
    </main>
  );
}
