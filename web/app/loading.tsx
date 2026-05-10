const COLUMN_COUNT = 5;
const CARDS_PER_COLUMN = 3;

export default function Loading() {
  return (
    <main className="min-h-screen bg-white px-6 py-5">
      <header className="mb-4 flex flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <div className="h-6 w-48 animate-pulse rounded bg-zinc-200" />
          <div className="h-4 w-24 animate-pulse rounded bg-zinc-100" />
        </div>
        <div className="h-9 w-full max-w-xl animate-pulse rounded bg-zinc-100" />
      </header>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: COLUMN_COUNT }).map((_, colIdx) => (
          <section
            key={colIdx}
            className="flex min-w-0 flex-col rounded-md bg-zinc-50/60 p-2.5"
          >
            <div className="mb-2 flex items-center gap-1.5 border-b border-zinc-200 pb-2 px-1">
              <div className="h-3 w-16 animate-pulse rounded bg-zinc-200" />
            </div>
            <div className="flex flex-col gap-1.5">
              {Array.from({ length: CARDS_PER_COLUMN }).map((__, cardIdx) => (
                <div
                  key={cardIdx}
                  className="h-[68px] animate-pulse rounded-md bg-zinc-100"
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
