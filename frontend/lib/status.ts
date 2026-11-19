export const statusStyles: Record<string, string> = {
  contradiction: "border-red-500/30 bg-red-500/10 text-red-400",
  consistent: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  inconclusive: "border-zinc-500/30 bg-zinc-500/10 text-zinc-400",
  insufficient_signal:
    "border-dashed border-zinc-500/40 bg-zinc-500/10 text-zinc-400",
  not_applicable: "border-zinc-700 bg-zinc-800/50 text-zinc-500",
};

export const statusLabels: Record<string, string> = {
  contradiction: "Contradiction",
  consistent: "Consistent",
  inconclusive: "Inconclusive",
  insufficient_signal: "Insufficient signal",
  not_applicable: "Not applicable",
};