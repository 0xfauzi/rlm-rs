import { Suspense } from "react";
import { CitationsPageClient } from "./CitationsPageClient";
import { SkeletonCard, SkeletonText } from "../../components/ui/Skeleton";

export default function CitationsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <SkeletonCard lines={3} />
          <SkeletonText lines={5} />
        </div>
      }
    >
      <CitationsPageClient />
    </Suspense>
  );
}
