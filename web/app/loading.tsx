const COLUMN_COUNT = 5;
const CARDS_PER_COLUMN = 3;

export default function Loading() {
  return (
    <main className="min-h-screen bg-white p-6">
      <header className="mb-4 flex flex-col gap-3">
        <div className="flex items-baseline gap-3">
          <div className="h-7 w-48 animate-pulse rounded bg-zinc-200" />
          <div className="h-5 w-16 animate-pulse rounded bg-zinc-100" />
        </div>
        <div className="h-9 w-full max-w-xl animate-pulse rounded bg-zinc-100" />
      </header>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: COLUMN_COUNT }).map((_, colIdx) => (
          <section
            key={colIdx}
            className="flex min-w-0 flex-col gap-2 rounded-lg bg-zinc-50 p-3"
          >
            <div className="h-5 w-24 animate-pulse rounded bg-zinc-200" />
            <div className="flex flex-col gap-2">
              {Array.from({ length: CARDS_PER_COLUMN }).map((__, cardIdx) => (
                <div
                  key={cardIdx}
                  className="h-20 animate-pulse rounded border border-zinc-200 bg-white"
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
