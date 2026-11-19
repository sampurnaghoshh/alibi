
import { getInvestigation } from "@/lib/api";
import Link from "next/link";
import EvidenceGraph from "./EvidenceGraph";
import { statusStyles, statusLabels } from "@/lib/status";

export default async function InvestigationPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const investigation = await getInvestigation(id);
  const { assessment, findings, claims, inputs } = investigation;

const caption = investigation.caption;

const highlightedCaption = [...claims]
  .sort((a, b) => a.source.start - b.source.start)
  .reduce(
    (parts, claim) => {
      const previousEnd = parts[parts.length - 1].end;
      const start = Math.max(previousEnd, claim.source.start);
      const end = Math.max(start, claim.source.end);

      if (start > previousEnd) {
        parts.push({
          text: caption.slice(previousEnd, start),
          highlighted: false,
          end: start,
        });
      }

      if (end > start) {
        parts.push({
          text: caption.slice(start, end),
          highlighted: true,
          end,
        });
      }

      return parts;
    },
    [{ text: "", highlighted: false, end: 0 }]
  )
  .slice(1);

const lastEnd = highlightedCaption[highlightedCaption.length - 1]?.end ?? 0;

if (lastEnd < caption.length) {
  highlightedCaption.push({
    text: caption.slice(lastEnd),
    highlighted: false,
    end: caption.length,
  });
}


  return (
    <main className="min-h-screen bg-[#0D1012] text-zinc-100">
      <header className="border-b border-white/10 bg-[#0d1017]">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-5">
          <div>
            <Link href="/" className="text-2xl font-bold tracking-[0.25em]">
              ALIBI<span className="text-cyan-400">.</span>
            </Link>
            <p className="mt-1 text-xs tracking-widest text-zinc-500">
              INVESTIGATION REPORT
            </p>
          </div>
          <Link href="/" className="text-sm text-cyan-400 hover:text-cyan-300">
            Back to dashboard
          </Link>
        </div>
      </header>

      <div className="mx-auto max-w-7xl space-y-8 px-6 py-8">
        <section>
          <p className="mb-3 text-xs font-semibold tracking-[0.2em] text-red-400">
           {assessment.human_review === "priority"
  ? "PRIORITY HUMAN REVIEW"
  : "STANDARD HUMAN REVIEW"}
          </p>
          <div className="rounded-xl border border-red-500/20 bg-[#15191D] p-6">
            <p className="text-xs tracking-widest text-zinc-500">
              INVESTIGATION ASSESSMENT
            </p>
            <h1 className="mt-3 max-w-3xl text-2xl font-semibold leading-relaxed">
              {assessment.headline_text}
            </h1>
            <p className="mt-3 text-sm text-zinc-400">
  Human review required. This is an evidence report, not a verdict.
</p>
          </div>
        </section>


<section className="rounded-xl border border-white/10 bg-[#131720] p-6">
  <p className="mb-3 text-xs font-semibold tracking-widest text-zinc-500">
    ORIGINAL CAPTION
  </p>

  <p className="text-lg leading-relaxed text-zinc-300">
    {highlightedCaption.map((part, index) =>
      part.highlighted ? (
        <mark
          key={index}
          className="rounded bg-cyan-400/20 px-1 text-cyan-300 underline decoration-cyan-400/60 underline-offset-4"
        >
          {part.text}
        </mark>
      ) : (
        <span key={index}>{part.text}</span>
      )
    )}
  </p>

  <p className="mt-4 text-xs text-zinc-500">
    Highlighted text represents extracted claim spans from the original caption.
  </p>
</section>
<section className="grid gap-5 md:grid-cols-3">
  <div className="rounded-xl border border-white/10 bg-[#131720] p-5">
    <p className="text-xs tracking-widest text-zinc-500">
      EXPERIMENTAL INDEX
    </p>

    {assessment.experimental_score ? (
      <>
        <p className="mt-2 text-xl font-medium text-zinc-400">
          {assessment.experimental_score.value}
          <span className="text-sm text-zinc-500">/100</span>
        </p>
        <p className="mt-3 text-xs text-zinc-500">
          {assessment.experimental_score.label}
        </p>
      </>
    ) : (
      <p className="mt-2 text-sm text-zinc-500">Not available</p>
    )}
  </div>

  <div className="rounded-xl border border-white/10 bg-[#131720] p-5 md:col-span-2">
    <p className="text-xs tracking-widest text-zinc-500">MEDIA INPUT</p>
    {inputs.map((file) => (
      <div key={file.file_id} className="mt-4">
        <p className="font-medium">{file.name}</p>
        <p className="mt-1 text-xs text-zinc-500">
          {file.mime} · {(file.size / 1024 / 1024).toFixed(2)} MB ·{" "}
          {file.duration_s !== null
            ? `${file.duration_s}s`
            : "Duration unavailable"}
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
              <div
                key={claim.id}
                className="rounded-xl border border-white/10 bg-[#131720] p-5"
              >
                <p className="text-xs uppercase tracking-widest text-zinc-500">
                  {claim.type.replaceAll("_", " ")}
                </p>
                <p className="mt-3 text-xl font-semibold">{claim.value}</p>
                <p className="mt-2 text-xs text-zinc-500">
                  Extracted from caption · {claim.extracted_by}
                </p>
              </div>
            ))}
          </div>
        </section>
        <EvidenceGraph />

        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold">Evidence witnesses</h2>
            <span className="text-xs text-zinc-500">
              {findings.length} indicators
            </span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {findings.map((finding) => (
              <article
                key={finding.id}
                className="rounded-xl border border-white/10 bg-[#131720] p-5"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs font-semibold uppercase tracking-widest text-zinc-400">
                    {finding.witness.replaceAll("_", " ")}
                  </span>
                  <span
                    className={`rounded-full border px-3 py-1 text-xs ${
                      statusStyles[finding.status]
                    }`}
                  >
                    {statusLabels[finding.status]}
                  </span>
                </div>
                <h3 className="mt-4 font-semibold leading-relaxed">
                  {finding.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-zinc-400">
                  {finding.summary}
                </p>

<details className="mt-4 rounded-lg border border-white/10 bg-[#0d1017] p-4">
  <summary className="cursor-pointer text-sm font-medium text-cyan-400">
    Why? View evidence and limitations
  </summary>

  <div className="mt-4 space-y-4">
    <div>
      <h4 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
        Technical details
      </h4>
      <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-400">
        {JSON.stringify(finding.details, null, 2)}
      </pre>
    </div>

    <div>
      <h4 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
        Supporting evidence
      </h4>
      {finding.evidence.map((item, index) => (
        <p key={index} className="mt-2 text-sm text-zinc-400">
          {item.label}
        </p>
      ))}
    </div>

    <div>
      <h4 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
        Alternative explanations
      </h4>
      {finding.alternatives.map((item, index) => (
        <p key={index} className="mt-2 text-sm leading-relaxed text-zinc-400">
          • {item}
        </p>
      ))}
    </div>

    <div>
      <h4 className="text-xs font-semibold uppercase tracking-widest text-zinc-500">
        Limitations
      </h4>
      {finding.limitations.map((item, index) => (
        <p key={index} className="mt-2 text-sm leading-relaxed text-zinc-400">
          • {item}
        </p>
      ))}
    </div>
  </div>
</details>

                <div className="mt-4 border-t border-white/10 pt-3">
                  <p className="text-xs text-zinc-500">
                    Reliability:{" "}
                    <span className="text-zinc-300">{finding.reliability}</span>
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
