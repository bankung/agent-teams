const COLUMN_COUNT = 5;
const CARDS_PER_COLUMN = 3;

export default function Loading() {
  return (
    <main className="flex h-screen flex-col overflow-hidden bg-white dark:bg-zinc-950 px-6 py-5">
      <header className="mb-4 flex flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <div className="h-6 w-48 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
          <div className="h-4 w-24 animate-pulse rounded bg-zinc-100 dark:bg-zinc-900" />
        </div>
        <div className="h-9 w-full max-w-xl animate-pulse rounded bg-zinc-100 dark:bg-zinc-900" />
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-hidden md:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: COLUMN_COUNT }).map((_, colIdx) => (
          <section
            key={colIdx}
            className="flex min-h-0 min-w-0 flex-col rounded-md bg-zinc-50/60 dark:bg-zinc-900/40 p-2.5"
          >
            <div className="mb-2 flex items-center gap-1.5 border-b border-zinc-200 dark:border-zinc-800 pb-2 px-1">
              <div className="h-3 w-16 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
              {Array.from({ length: CARDS_PER_COLUMN }).map((__, cardIdx) => (
                <div
                  key={cardIdx}
                  className="h-[68px] animate-pulse rounded-md bg-zinc-100 dark:bg-zinc-800"
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
