import { Suspense } from "react";
import { ExecutionsPageClient } from "./ExecutionsPageClient";
import { SkeletonCard, SkeletonTable } from "../../components/ui/Skeleton";

export default function ExecutionsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <SkeletonCard lines={3} />
          <SkeletonTable rows={5} columns={7} />
        </div>
      }
    >
      <ExecutionsPageClient />
    </Suspense>
  );
}
