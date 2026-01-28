"use client";

import { useCallback, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ErrorBoundary } from "../ErrorBoundary";

export function AppShell({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const handleToggleSidebar = useCallback(() => {
    setSidebarOpen((open) => !open);
  }, []);

  const handleCloseSidebar = useCallback(() => {
    setSidebarOpen(false);
  }, []);

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f8fafc_0%,#eef2ff_100%)] text-slate-900">
      <TopBar onToggleSidebar={handleToggleSidebar} />
      <div className="relative flex">
        {sidebarOpen ? (
          <button
            type="button"
            aria-label="Close sidebar"
            className="fixed inset-0 z-30 bg-black/20 md:hidden"
            onClick={handleCloseSidebar}
          />
        ) : null}
        <Sidebar isOpen={sidebarOpen} onClose={handleCloseSidebar} />
        <main className="flex-1 px-3 py-8 md:px-4 xl:px-6">
          <div className="mx-auto w-full max-w-none rounded-3xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
            <ErrorBoundary>{children}</ErrorBoundary>
          </div>
        </main>
      </div>
    </div>
  );
}
