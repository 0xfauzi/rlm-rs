"use client";

export type ToastVariant = "success" | "error" | "info";

export interface ToastItem {
  id: string;
  message: string;
  variant: ToastVariant;
}

const VARIANT_STYLES: Record<ToastVariant, string> = {
  success: "border-emerald-500/40 bg-emerald-500/15 text-emerald-950",
  error: "border-rose-500/40 bg-rose-500/15 text-rose-950",
  info: "border-sky-500/40 bg-sky-500/15 text-sky-950",
};

const VARIANT_GLOW: Record<ToastVariant, string> = {
  success: "shadow-[0_12px_30px_-18px_rgba(16,185,129,0.8)]",
  error: "shadow-[0_12px_30px_-18px_rgba(244,63,94,0.7)]",
  info: "shadow-[0_12px_30px_-18px_rgba(14,165,233,0.7)]",
};

interface ToastViewportProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

export function ToastViewport({ toasts, onDismiss }: ToastViewportProps) {
  if (toasts.length === 0) {
    return null;
  }

  return (
    <div className="fixed right-6 top-6 z-50 flex w-[min(360px,90vw)] flex-col gap-3">
      {toasts.map((toast) => (
        <button
          key={toast.id}
          type="button"
          onClick={() => onDismiss(toast.id)}
          className={`group w-full rounded-2xl border px-4 py-3 text-left text-sm font-medium backdrop-blur transition hover:-translate-y-0.5 hover:shadow-xl ${
            VARIANT_STYLES[toast.variant]
          } ${VARIANT_GLOW[toast.variant]}`}
        >
          <div className="flex items-start justify-between gap-3">
            <span>{toast.message}</span>
            <span className="text-xs uppercase tracking-[0.3em] opacity-60">
              {toast.variant}
            </span>
          </div>
          <div className="mt-2 h-0.5 w-full rounded-full bg-black/5" />
        </button>
      ))}
    </div>
  );
}
