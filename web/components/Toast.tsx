"use client";

import { useEffect } from "react";

export type ToastMessage = { id: number; text: string };

type Props = {
  messages: ToastMessage[];
  onDismiss: (id: number) => void;
};

// Auto-dismiss after 4s. Shadow allowed here (floating chrome, not card chrome).
export function ToastStack({ messages, onDismiss }: Props) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2"
    >
      {messages.map((m) => (
        <ToastItem key={m.id} message={m} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function ToastItem({
  message,
  onDismiss,
}: {
  message: ToastMessage;
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    const t = setTimeout(() => onDismiss(message.id), 4000);
    return () => clearTimeout(t);
  }, [message.id, onDismiss]);
  return (
    <div
      data-toast
      className="rounded-md bg-zinc-900 px-3 py-2 text-sm text-white shadow-sm"
    >
      {message.text}
    </div>
  );
}
