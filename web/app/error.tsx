"use client";

type Props = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function Error({ error, reset }: Props) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white dark:bg-zinc-950 px-6 py-5">
      <div className="max-w-xl rounded-md border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Failed to load board
        </h2>
        <p className="mt-1 break-words text-sm text-red-700 dark:text-red-300">
          {error.message || "Unknown error"}
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
            digest: {error.digest}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={reset}
        className="rounded-md bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
      >
        Retry
      </button>
    </main>
  );
}
