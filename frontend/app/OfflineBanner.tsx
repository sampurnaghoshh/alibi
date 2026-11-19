import { OFFLINE_MESSAGE } from "@/lib/api";

/** Calm notice when the analysis server (the laptop behind the tunnel) can't be reached. */
export default function OfflineBanner() {
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-xl border border-white/10 bg-[#15191D] px-4 py-3 text-sm leading-6 text-zinc-300"
    >
      <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#929CAA]" aria-hidden="true" />
      <p>{OFFLINE_MESSAGE}</p>
    </div>
  );
}
