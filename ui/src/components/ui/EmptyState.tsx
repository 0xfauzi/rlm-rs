import React from "react";
import Link from "next/link";

interface EmptyStateAction {
  label: string;
  href?: string;
  onClick?: () => void;
}

interface EmptyStateProps {
  title: string;
  description: string;
  icon?: React.ReactNode;
  action?: EmptyStateAction;
}

function DefaultIcon() {
  return (
    <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100">
      <svg
        viewBox="0 0 24 24"
        className="h-6 w-6 text-slate-500"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path d="M12 6v12" strokeLinecap="round" />
        <path d="M6 12h12" strokeLinecap="round" />
      </svg>
    </div>
  );
}

export function EmptyState({ title, description, icon, action }: EmptyStateProps) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-6 text-center">
      <div className="mx-auto flex justify-center">{icon ?? <DefaultIcon />}</div>
      <h3 className="mt-4 text-sm font-semibold text-slate-700">{title}</h3>
      <p className="mt-2 text-xs text-slate-500">{description}</p>
      {action ? (
        action.href ? (
          <Link
            href={action.href}
            className="mt-4 inline-flex items-center justify-center rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600"
          >
            {action.label}
          </Link>
        ) : (
          <button
            type="button"
            onClick={action.onClick}
            className="mt-4 inline-flex items-center justify-center rounded-full border border-slate-200 px-4 py-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600"
          >
            {action.label}
          </button>
        )
      ) : null}
    </div>
  );
}
