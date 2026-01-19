import React from "react";

interface SkeletonTextProps {
  lines?: number;
  className?: string;
}

export function SkeletonText({ lines = 3, className = "" }: SkeletonTextProps) {
  const rows = Math.max(1, lines);
  return (
    <div className={`space-y-2 ${className}`.trim()}>
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={`skeleton-line-${index}`}
          className={`h-3 rounded-full bg-slate-200/70 animate-pulse ${
            index === rows - 1 ? "w-3/4" : "w-full"
          }`}
        />
      ))}
    </div>
  );
}

interface SkeletonCardProps {
  lines?: number;
  className?: string;
}

export function SkeletonCard({ lines = 4, className = "" }: SkeletonCardProps) {
  return (
    <div
      className={`rounded-3xl border border-slate-200 bg-white p-6 shadow-sm ${className}`.trim()}
    >
      <div className="h-4 w-32 rounded-full bg-slate-200/70 animate-pulse" />
      <div className="mt-4">
        <SkeletonText lines={lines} />
      </div>
    </div>
  );
}

interface SkeletonTableProps {
  rows?: number;
  columns?: number;
  className?: string;
}

export function SkeletonTable({
  rows = 4,
  columns = 4,
  className = "",
}: SkeletonTableProps) {
  const safeRows = Math.max(1, rows);
  const safeColumns = Math.max(1, columns);
  return (
    <div className={`space-y-3 ${className}`.trim()}>
      <div
        className="grid gap-3"
        style={{ gridTemplateColumns: `repeat(${safeColumns}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: safeColumns }).map((_, index) => (
          <div
            key={`skeleton-header-${index}`}
            className="h-3 rounded-full bg-slate-200/70 animate-pulse"
          />
        ))}
      </div>
      {Array.from({ length: safeRows }).map((_, rowIndex) => (
        <div
          key={`skeleton-row-${rowIndex}`}
          className="grid gap-3"
          style={{ gridTemplateColumns: `repeat(${safeColumns}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: safeColumns }).map((__, colIndex) => (
            <div
              key={`skeleton-cell-${rowIndex}-${colIndex}`}
              className="h-4 rounded-full bg-slate-200/70 animate-pulse"
            />
          ))}
        </div>
      ))}
    </div>
  );
}
