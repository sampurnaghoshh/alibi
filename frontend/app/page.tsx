
"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  getDemoCases,
  runDemoCase,
  uploadInvestigation,
  type DemoCase,
} from "@/lib/api";

/** Today's date in the browser's own time zone, as YYYY-MM-DD (what <input type="date"> uses). */
function localToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export default function Home() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);

  const [files, setFiles] = useState<File[]>([]);
  const [caption, setCaption] = useState("");
  const [sharedOn, setSharedOn] = useState("");
  const [demoCases, setDemoCases] = useState<DemoCase[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeDemo, setActiveDemo] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [demoError, setDemoError] = useState("");

  // Default "Shared on" to today. Set after mount: this page is pre-rendered at build time, so a
  // value computed during rendering would be the build date.
  useEffect(() => {
    setSharedOn((current) => current || localToday());
  }, []);

  useEffect(() => {
    getDemoCases()
      .then(setDemoCases)
      .catch((err) => {
        setDemoError(
          err instanceof Error ? err.message : "Could not load demo cases."
        );
      });
  }, []);

  function addFiles(selected: FileList | File[]) {
    const incoming = Array.from(selected);
    setFiles((current) => [...current, ...incoming].slice(0, 5));
    setError("");
  }

  function removeFile(index: number) {
    setFiles((current) => current.filter((_, i) => i !== index));
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (!files.length || !caption.trim() || !sharedOn) {
      setError("Add at least one file, a caption, and the date it was shared.");
      return;
    }

    setLoading(true);

    try {
      // Send the date exactly as picked ("2026-11-19"): the backend resolves relative times
      // ("yesterday evening") in IST. new Date(...).toISOString() would turn it into UTC.
      const investigation = await uploadInvestigation(
        files,
        caption.trim(),
        sharedOn
      );

      router.push(`/investigation/${investigation.id}`);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Investigation could not be started."
      );
      setLoading(false);
    }
  }

  async function handleDemo(demo: DemoCase) {
    if (!demo.ready || activeDemo || loading) return;

    setError("");
    setActiveDemo(demo.name);

    try {
      const investigation = await runDemoCase(demo.name);
      router.push(`/investigation/${investigation.id}`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Demo investigation failed."
      );
      setActiveDemo(null);
    }
  }

  const displayedCases = demoCases.slice(0, 2);

  return (
    <main className="min-h-screen bg-[#0D1012] text-zinc-100">
      <header className="border-b border-white/10 bg-[#15191D]">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
          <div>
            <h1 className="text-2xl font-bold tracking-[0.25em]">
              ALIBI<span className="text-[#83B6D1]">.</span>
            </h1>
            <p className="mt-1 text-[10px] tracking-[0.25em] text-zinc-500">
              MEDIA CONTEXT INVESTIGATION
            </p>
          </div>
          <span className="text-xs text-zinc-500">Evidence-led analysis</span>
        </div>
      </header>

      <div className="mx-auto max-w-3xl px-6 py-14">
        <div className="mb-10">
          <p className="mb-3 text-xs font-medium tracking-[0.2em] text-[#83B6D1]">
            NEW INVESTIGATION
          </p>
          <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            Examine the context.
          </h2>
          <p className="mt-3 max-w-xl text-sm leading-7 text-zinc-400">
            Upload media and its original caption. ALIBI examines available
            evidence and context to help assess the claims being made.
          </p>
        </div>

        <form onSubmit={handleUpload} className="space-y-7">
          <section>
            <div className="mb-3 flex items-center justify-between">
              <label className="text-sm font-medium">Media files</label>
              <span className="text-xs text-zinc-500">Up to 5 files</span>
            </div>

            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                addFiles(event.dataTransfer.files);
              }}
              onClick={() => fileInput.current?.click()}
              className={`cursor-pointer rounded-xl border border-dashed p-10 text-center transition ${
                dragging
                  ? "border-[#83B6D1] bg-[#83B6D1]/10"
                  : "border-white/20 bg-[#15191D] hover:border-[#83B6D1]/60"
              }`}
            >
              <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-white/5 text-xl text-[#83B6D1]">
                +
              </div>
              <p className="text-sm font-medium">
                Drop your files here or{" "}
                <span className="text-[#83B6D1]">browse</span>
              </p>
              <p className="mt-2 text-xs text-zinc-500">
                Images, video and audio supported
              </p>
              <input
                ref={fileInput}
                type="file"
                multiple
                accept="image/*,video/*,audio/*"
                className="hidden"
                onChange={(event) => {
                  if (event.target.files) addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
            </div>

            {files.length > 0 && (
              <div className="mt-3 space-y-2">
                {files.map((file, index) => (
                  <div
                    key={`${file.name}-${index}`}
                    className="flex items-center justify-between rounded-lg border border-white/10 bg-[#15191D] px-4 py-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm">{file.name}</p>
                      <p className="text-xs text-zinc-500">
                        {(file.size / (1024 * 1024)).toFixed(2)} MB
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeFile(index)}
                      className="ml-4 text-xs text-zinc-500 hover:text-red-400"
                    >
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section>
            <label htmlFor="caption" className="mb-3 block text-sm font-medium">
              Original caption
            </label>
            <textarea
              id="caption"
              value={caption}
              onChange={(event) => setCaption(event.target.value)}
              placeholder="Paste the original post caption or claim..."
              rows={5}
              className="w-full resize-y rounded-xl border border-white/10 bg-[#15191D] px-4 py-3 text-sm leading-6 outline-none transition placeholder:text-zinc-600 focus:border-[#83B6D1]/60"
            />
          </section>

          <section>
            <label htmlFor="sharedOn" className="mb-3 block text-sm font-medium">
              Shared on
            </label>
            <input
              id="sharedOn"
              type="date"
              value={sharedOn}
              onChange={(event) => setSharedOn(event.target.value)}
              className="w-full rounded-xl border border-white/10 bg-[#15191D] px-4 py-3 text-sm text-zinc-200 outline-none focus:border-[#83B6D1]/60"
            />
            <p className="mt-2 text-xs text-zinc-500">
              When was this media or post originally shared?
            </p>
          </section>

          {error && (
            <div
              role="alert"
              className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"
            >
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-xl bg-[#83B6D1] px-5 py-4 text-sm font-semibold text-[#0D1012] transition hover:bg-[#9CC8DF] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Starting investigation..." : "Investigate"}
          </button>
        </form>

        <div className="my-10 flex items-center gap-4">
          <div className="h-px flex-1 bg-white/10" />
          <span className="text-xs tracking-widest text-zinc-500">
            OR TRY A DEMO
          </span>
          <div className="h-px flex-1 bg-white/10" />
        </div>

        <section>
          <div className="mb-4">
            <h3 className="text-sm font-medium">Explore demo investigations</h3>
            <p className="mt-1 text-xs text-zinc-500">
              Try a prepared example to see how ALIBI works.
            </p>
          </div>

          {demoError && (
            <p className="mb-3 text-xs text-zinc-500">{demoError}</p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            {displayedCases.map((demo, index) => (
              <button
                key={demo.name}
                type="button"
                disabled={!demo.ready || loading || activeDemo !== null}
                onClick={() => handleDemo(demo)}
                title={
                  !demo.ready
                    ? demo.problem || "This demo is unavailable."
                    : ""
                }
                className="rounded-xl border border-white/10 bg-[#15191D] p-5 text-left transition hover:border-[#83B6D1]/50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <span className="text-xs tracking-widest text-[#83B6D1]">
                  CASE {index === 0 ? "A" : "B"}
                </span>
                <p className="mt-3 text-sm font-medium">
                  {activeDemo === demo.name
                    ? "Starting investigation..."
                    : demo.name}
                </p>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-zinc-500">
                  {demo.caption}
                </p>
                {!demo.ready && (
                  <p className="mt-3 text-xs text-zinc-600">
                    Currently unavailable
                  </p>
                )}
              </button>
            ))}

            {demoCases.length === 0 && !demoError && (
              <p className="text-xs text-zinc-500">Loading demo cases...</p>
            )}
          </div>
        </section>

        <footer className="mt-14 border-t border-white/10 pt-6 text-center text-xs leading-6 text-zinc-600">
          Human review required. This is an evidence report, not a verdict.
        </footer>
      </div>
    </main>
  );
}
