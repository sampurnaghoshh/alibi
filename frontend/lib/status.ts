export const statusStyles: Record<string, string> = {
  contradiction: "border-[#D96B68]/30 bg-[#D96B68]/10 text-[#D96B68]",
  consistent: "border-[#69B88A]/30 bg-[#69B88A]/10 text-[#69B88A]",
  inconclusive: "border-[#929CAA]/30 bg-[#929CAA]/10 text-[#929CAA]",
  insufficient_signal:
    "border-dashed border-[#929CAA]/40 bg-[#929CAA]/10 text-[#929CAA]",
  not_applicable: "border-[#929CAA]/20 bg-[#929CAA]/5 text-[#929CAA]/60",
};

export const statusLabels: Record<string, string> = {
  contradiction: "Contradiction",
  consistent: "Consistent",
  inconclusive: "Inconclusive",
  insufficient_signal: "Insufficient signal",
  not_applicable: "Not applicable",
};