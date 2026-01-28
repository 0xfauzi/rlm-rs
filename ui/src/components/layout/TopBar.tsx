"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useApp } from "../../contexts/AppContext";

const STATUS_LABEL = {
  online: "Online",
  offline: "Offline",
  unknown: "Offline",
} as const;

const NAV_LINKS = [
  { label: "Sessions", href: "/sessions" },
  { label: "Executions", href: "/executions" },
  { label: "Debug", href: "/debug" },
];

function isActive(pathname: string, href: string) {
  if (href === "/") {
    return pathname === href;
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

function HealthBadge({
  label,
  status,
  onClick,
}: {
  label: string;
  status: "online" | "offline" | "unknown";
  onClick: () => void;
}) {
  const display = STATUS_LABEL[status] ?? "Offline";
  const dotClass = status === "online" ? "bg-emerald-500" : "bg-rose-500";
  const badgeClass = status === "online" ? "border-emerald-200" : "border-rose-200";

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-wide ${badgeClass} bg-white/80 text-slate-800 shadow-sm transition hover:shadow-md`}
    >
      <span className={`h-2 w-2 rounded-full ${dotClass}`} />
      <span>{label}</span>
      <span className="text-slate-500">{display}</span>
    </button>
  );
}

export function TopBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const router = useRouter();
  const pathname = usePathname();
  const { apiHealth, localstackHealth, config, runningExecutionsCount } = useApp();

  const goToDebug = () => {
    router.push("/debug");
  };

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b border-slate-200 bg-white/80 px-4 backdrop-blur md:px-6">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onToggleSidebar}
          className="flex h-9 w-9 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:bg-slate-100 md:hidden"
          aria-label="Toggle navigation"
        >
          <span className="block h-0.5 w-4 rounded-full bg-slate-700" />
          <span className="mt-1 block h-0.5 w-4 rounded-full bg-slate-700" />
          <span className="mt-1 block h-0.5 w-4 rounded-full bg-slate-700" />
        </button>
        <div className="flex flex-col">
          <span className="text-sm font-semibold uppercase tracking-[0.3em] text-slate-500">
            RLM
          </span>
          <span className="text-xs text-slate-400">Recursive Language Model</span>
        </div>
      </div>
      <nav className="hidden flex-1 items-center justify-center gap-2 md:flex">
        {NAV_LINKS.map((link) => {
          const active = pathname ? isActive(pathname, link.href) : false;
          const activeClass = active
            ? "border-slate-900 bg-slate-900 text-white"
            : "border-slate-200 bg-white text-slate-600 hover:border-slate-400";
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center gap-2 rounded-full border px-4 py-2 text-xs font-semibold uppercase tracking-[0.25em] transition ${activeClass}`}
            >
              <span>{link.label}</span>
              {link.label === "Executions" && runningExecutionsCount > 0 ? (
                <span className="rounded-full bg-amber-200 px-2 py-0.5 text-xs font-semibold text-amber-800">
                  {runningExecutionsCount}
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>
      <div className="flex items-center gap-3">
        <HealthBadge label="API" status={apiHealth} onClick={goToDebug} />
        <HealthBadge label="LocalStack" status={localstackHealth} onClick={goToDebug} />
        <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">
          <span className="text-slate-500">Tenant:</span>
          <span>{config.tenant}</span>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs font-semibold text-slate-700">
          <span className="text-slate-500">Using dev key:</span>
          <span>{config.devKey}</span>
        </div>
      </div>
    </header>
  );
}
