"use client";

type Props = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function Error({ error, reset }: Props) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-white p-6">
      <div className="max-w-xl rounded border border-red-200 bg-red-50 p-4">
        <h2 className="text-lg font-semibold text-red-800">
          Failed to load board
        </h2>
        <p className="mt-1 break-words text-sm text-red-700">
          {error.message || "Unknown error"}
        </p>
        {error.digest && (
          <p className="mt-2 font-mono text-xs text-red-600">
            digest: {error.digest}
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={reset}
        className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800"
      >
        Retry
      </button>
    </main>
  );
}
