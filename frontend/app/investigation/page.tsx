"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { LoaderCircle } from "lucide-react";
import { getInvestigationState, type InvestigationState } from "@/lib/api";
import InvestigationView, { Problem } from "./InvestigationView";
import StillRunning from "./StillRunning";

/**
 * /investigation/?id=inv_... : the app is a static export (one URL serves the frontend, /live and
 * /api/*), so the investigation is chosen by a query parameter and loaded in the browser.
 */
export default function InvestigationPage() {
  return (
    <Suspense fallback={<Loading />}>
      <InvestigationLoader />
    </Suspense>
  );
}

function Loading() {
  return (
    <main className="min-h-screen bg-[#0D1012] text-zinc-100">
      <div className="flex justify-center py-40">
        <LoaderCircle className="h-7 w-7 animate-spin text-[#83B6D1]" aria-label="Loading" />
      </div>
    </main>
  );
}

function InvestigationLoader() {
  const id = useSearchParams().get("id") ?? "";
  const [state, setState] = useState<InvestigationState | null>(null);
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    getInvestigationState(id).then((s) => {
      if (alive) setState(s);
    });
    return () => {
      alive = false;
    };
  }, [id, attempt]);

  if (!id) {
    return <Problem title="No investigation selected" message="Open an investigation from the upload page or a demo case." />;
  }
  if (!state) return <Loading />;
  if (state.status === "running") return <StillRunning id={id} onDone={reload} />;   // 202: poll until done
  if (state.status === "missing") {
    return <Problem title="Investigation not found" message={state.message} />;
  }
  if (state.status === "error") {
    return <Problem title="This investigation could not be shown" message={state.message} />;
  }
  return <InvestigationView investigation={state.investigation} />;
}
