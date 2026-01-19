export default function Home() {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
            Overview
          </p>
          <h1 className="text-2xl font-semibold text-slate-900">
            Runtime control center
          </h1>
        </div>
        <div className="rounded-full border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-600 shadow-sm">
          Status: Awaiting sessions
        </div>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">Next steps</h2>
          <p className="mt-2 text-sm text-slate-600">
            Use the Sessions area to upload new documents and start Answerer or
            Runtime executions.
          </p>
        </div>
        <div className="rounded-2xl border border-slate-100 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">System focus</h2>
          <p className="mt-2 text-sm text-slate-600">
            Monitor API and LocalStack readiness from the top bar or jump into
            the Debug panel for deeper diagnostics.
          </p>
        </div>
      </div>
    </section>
  );
}
