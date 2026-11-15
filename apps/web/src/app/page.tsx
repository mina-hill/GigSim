import { Suspense } from "react";

import { WorkspaceAccess } from "@/components/workspace-access";

export default function Home() {
  return (
    <Suspense fallback={<main className="initial-loading">Loading GigBid…</main>}>
      <WorkspaceAccess />
    </Suspense>
  );
}
