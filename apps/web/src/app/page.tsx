import { Suspense } from "react";

import { Dashboard } from "@/components/dashboard";

export default function Home() {
  return (
    <Suspense fallback={<main className="initial-loading">Loading GigBid…</main>}>
      <Dashboard />
    </Suspense>
  );
}
