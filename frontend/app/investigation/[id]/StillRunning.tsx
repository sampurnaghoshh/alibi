"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import { API_URL } from "@/lib/api";

/**
 * Shown while GET /api/investigations/{id} answers 202 {status: "running"} (an async run, e.g. from
 * the Telegram bot). Checks again every 2 s and refreshes the page as soon as the answer changes,
 * so the finished investigation (or its error) is rendered by the server as usual.
 */
export default function StillRunning({ id }: { id: string }) {
  const router = useRouter();
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    let stopped = false;
    const timer = setInterval(async () => {
      setSeconds((s) => s + 2);
      try {
        const response = await fetch(
          `${API_URL}/api/investigations/${encodeURIComponent(id)}`,
          { cache: "no-store" }
        );
        if (!stopped && response.status !== 202) {
          stopped = true;
          clearInterval(timer);
          router.refresh();
        }
      } catch {
        // API briefly unreachable: keep checking.
      }
    }, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [id, router]);

  return (
    <main className="min-h-screen bg-[#0D1012] text-zinc-100">
      <div className="mx-auto flex max-w-xl flex-col items-center px-6 py-32 text-center">
        <LoaderCircle className="h-8 w-8 animate-spin text-[#83B6D1]" aria-hidden="true" />
        <h1 className="mt-6 text-2xl font-semibold">Investigation still running…</h1>
        <p className="mt-3 text-sm leading-6 text-zinc-400">
          The witnesses are still examining the evidence. This page updates by itself when the
          investigation is complete.
        </p>
        <p className="mt-4 font-mono text-xs text-zinc-500">
          {id} · waiting {seconds} s
        </p>
        <div className="mt-8 flex gap-4 text-sm">
          <a
            href={`${API_URL}/live?id=${encodeURIComponent(id)}`}
            className="text-[#83B6D1] hover:text-[#9CC8DF]"
          >
            Watch it on the Live Desk
          </a>
          <Link href="/" className="text-zinc-400 hover:text-zinc-200">
            New investigation
          </Link>
        </div>
        <p className="mt-12 text-xs text-zinc-600">
          Human review required. This is an evidence report, not a verdict.
        </p>
      </div>
    </main>
  );
}
