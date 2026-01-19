"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApp } from "../../contexts/AppContext";

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
}

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

export function Sidebar({ isOpen, onClose }: SidebarProps) {
  const pathname = usePathname();
  const { localstackHealth, runningExecutionsCount } = useApp();
  const statusClass =
    localstackHealth === "online" ? "bg-emerald-500" : "bg-rose-500";

  return (
    <aside
      className={`fixed left-0 top-0 z-40 flex h-full w-64 flex-col border-r border-slate-200 bg-white/90 backdrop-blur transition-transform md:static md:translate-x-0 ${
        isOpen ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex h-16 items-center justify-between border-b border-slate-100 px-5 md:hidden">
        <span className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
          Navigation
        </span>
        <button
          type="button"
          onClick={onClose}
          className="rounded-full border border-slate-200 p-2 text-slate-600"
          aria-label="Close navigation"
        >
          X
        </button>
      </div>
      <nav className="flex flex-1 flex-col gap-2 px-4 py-6">
        {NAV_LINKS.map((link) => {
          const active = pathname ? isActive(pathname, link.href) : false;
          const activeClass = active
            ? "bg-slate-900 text-white shadow"
            : "text-slate-700 hover:bg-slate-100";
          return (
            <Link
              key={link.href}
              href={link.href}
              onClick={onClose}
              className={`flex items-center justify-between rounded-2xl px-4 py-3 text-sm font-semibold transition ${activeClass}`}
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
      <div className="border-t border-slate-100 px-4 py-4">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          <span className={`h-2.5 w-2.5 rounded-full ${statusClass}`} />
          <span>LocalStack</span>
        </div>
      </div>
    </aside>
  );
}
