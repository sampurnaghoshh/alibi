
import investigation from "../../shared/fixtures/sample_investigation.json";

const statusStyles: Record<string, string> = {
  contradiction: "border-red-500/30 bg-red-500/10 text-[#D96B68]",
  consistent: "border-emerald-500/30 bg-emerald-500/10 text-[#69B88A]",
  inconclusive: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
  insufficient_signal: "border-amber-500/30 bg-amber-500/10 text-[#929CAA]",
  not_applicable: "border-zinc-700 bg-zinc-800/50 text-zinc-500",
};

const statusLabels: Record<string, string> = {
  contradiction: "Contradiction",
  consistent: "Consistent",
  inconclusive: "Inconclusive",
  insufficient_signal: "Insufficient signal",
  not_applicable: "Not applicable",
};

export default function Home() {
  const { assessment, findings, claims, inputs } = investigation;

  return (
    <main className="min-h-screen bg-[#0D1012] text-zinc-100">
      <header className="border-b border-white/10 bg-[#15191D]">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <div>
            <h1 className="text-2xl font-bold tracking-[0.25em]">
              ALIBI<span className="text-cyan-400">.</span>
            </h1>
            <p className="mt-1 text-xs tracking-widest text-zinc-500">
              MEDIA CONTEXT INVESTIGATION
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-zinc-500">INVESTIGATION ID</p>
            <p className="font-mono text-sm text-zinc-300">{investigation.id}</p>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-8 px-6 py-8">
        <section>
          <div className="mb-3 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-red-400" />
            <p className="text-xs font-semibold tracking-[0.2em] text-[#D96B68]">
              PRIORITY HUMAN REVIEW
            </p>
          </div>

          <div className="rounded-xl border border-red-500/20 bg-[#15191D] p-6">
            <p className="mb-3 text-xs tracking-widest text-zinc-500">
              INVESTIGATION ASSESSMENT
            </p>
            <h2 className="max-w-3xl text-2xl font-semibold leading-relaxed">
              {assessment.headline_text}
            </h2>
            <p className="mt-3 text-sm text-zinc-400">
              The available indicators should be reviewed together. Missing
              evidence does not establish that a claim is false.
            </p>
          </div>
        </section>

        <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {[
            ["Contradictions", assessment.counts.contradiction, "text-[#D96B68]"],
            ["Consistent", assessment.counts.consistent, "text-[#69B88A]"],
            ["Inconclusive", assessment.counts.inconclusive, "text-zinc-300"],
            ["Insufficient signal", assessment.counts.insufficient_signal, "text-[#929CAA]"],
            ["Not applicable", assessment.counts.not_applicable, "text-zinc-500"],
          ].map(([label, value, color]) => (
            <div key={label} className="rounded-xl border border-white/10 bg-[#15191D] p-4">
              <p className="text-xs text-zinc-500">{label}</p>
              <p className={`mt-2 text-3xl font-semibold ${color}`}>
                {value}
              </p>
            </div>
          ))}
        </section>

        <section className="grid gap-5 md:grid-cols-3">
          <div className="rounded-xl border border-white/10 bg-[#15191D] p-5">
            <p className="text-xs tracking-widest text-zinc-500">
              EXPERIMENTAL INDEX
            </p>
            <p className="mt-3 text-5xl font-semibold">
              {assessment.experimental_score.value}
              <span className="text-xl text-zinc-500">/100</span>
            </p>
            <p className="mt-3 text-xs leading-relaxed text-zinc-500">
              {assessment.experimental_score.label}
            </p>
          </div>

          <div className="rounded-xl border border-white/10 bg-[#15191D] p-5 md:col-span-2">
            <p className="text-xs tracking-widest text-zinc-500">
              MEDIA INPUT
            </p>
            {inputs.map((file) => (
              <div key={file.file_id} className="mt-4">
                <p className="font-medium">{file.name}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  VIDEO · {(file.size / 1024 / 1024).toFixed(2)} MB ·{" "}
                  {file.duration_s}s
                </p>
                <p className="mt-3 break-all font-mono text-xs text-zinc-600">
                  SHA-256: {file.sha256}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Extracted claims</h2>
            <span className="text-xs text-zinc-500">{claims.length} claims</span>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {claims.map((claim) => (
              <div key={claim.id} className="rounded-xl border border-white/10 bg-[#15191D] p-5">
                <p className="text-xs uppercase tracking-widest text-zinc-500">
                  {claim.type.replace("_", " ")}
                </p>
                <p className="mt-3 text-xl font-semibold">{claim.value}</p>
                <p className="mt-2 text-xs text-zinc-500">
                  Extracted from caption · {claim.extracted_by}
                </p>
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Evidence witnesses</h2>
            <span className="text-xs text-zinc-500">{findings.length} indicators</span>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {findings.map((finding) => (
              <article
                key={finding.id}
                className={`rounded-xl border border-white/10 bg-[#15191D] p-5 ${
                  finding.status === "not_applicable" ? "opacity-60" : ""
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold uppercase tracking-widest text-zinc-400">
                    {finding.witness.replace("_", " ")}
                  </span>
                  <span className={`rounded-full border px-3 py-1 text-xs ${
                    statusStyles[finding.status]
                  }`}>
                    {statusLabels[finding.status]}
                  </span>
                </div>

                <h3 className="mt-4 font-semibold leading-relaxed">
                  {finding.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                  {finding.summary}
                </p>

                <div className="mt-4 border-t border-white/10 pt-3">
                  <p className="text-xs text-zinc-500">
                    Reliability: <span className="text-zinc-300">{finding.reliability}</span>
                  </p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <footer className="border-t border-white/10 py-6 text-center text-xs text-zinc-600">
          ALIBI · Evidence-led media context investigation · Human review required
        </footer>
      </div>
    </main>
  );
}

