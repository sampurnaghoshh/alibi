
"use client";

import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";
import type { ReactNode } from "react";
import "@xyflow/react/dist/style.css";
import type { Graph } from "@/lib/types";

const statusColors: Record<string, string> = {
  contradiction: "#D96B68",
  consistent: "#69B88A",
  inconclusive: "#929CAA",
  insufficient_signal: "#929CAA",
  not_applicable: "#626975",
};

export default function EvidenceGraph({ graph }: { graph: Graph }) {
  const claims = graph.nodes.filter((node) => node.type === "claim");
  const witnesses = graph.nodes.filter((node) => node.type === "witness");

  const nodes: Node<{ label: ReactNode }>[] = graph.nodes.map((item) => {
    const index =
      item.type === "claim"
        ? claims.findIndex((node) => node.id === item.id)
        : witnesses.findIndex((node) => node.id === item.id);

    const position =
      item.type === "input"
        ? { x: 30, y: 230 }
        : item.type === "claim"
          ? { x: 390, y: index * 180 + 35 }
          : { x: 810, y: index * 105 + 10 };

    const color = statusColors[item.status ?? ""] ?? "#64748b";
    const reliability = item.meta?.reliability;

    return {
      id: item.id,
      position,
      data: {
        label: (
          <div className="min-w-0">
            <p className="mb-2 text-[9px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
              {item.type}
            </p>
            <p className="text-sm font-semibold text-zinc-200">
              {item.label}
            </p>
            {item.status && (
              <p className="mt-2 text-[10px]" style={{ color }}>
                {item.status.replaceAll("_", " ")}
              </p>
            )}
            {typeof reliability === "string" && (
              <p className="mt-1 text-[10px] text-zinc-500">
                Reliability: {reliability}
              </p>
            )}
          </div>
        ),
      },
      style: {
        width: 220,
        padding: 14,
        background: "#15191D",
        border:
          item.status === "insufficient_signal"
            ? `2px dashed ${color}99`
            : `1px solid ${color}66`,
      },
    };
  });

  const edges: Edge[] = graph.edges.map((edge) => {
    const isContradiction = edge.relation === "contradicts";
    const isSupport = edge.relation === "supports";
    const edgeColor = isContradiction
      ? "#D96B68"
      : isSupport
        ? "#69B88A"
        : "#929CAA";

    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: edge.relation.replaceAll("_", " "),
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: edgeColor,
      },
      style: {
        stroke: edgeColor,
        strokeWidth: 1.4,
        strokeDasharray:
          edge.relation === "inconclusive" ? "5 5" : undefined,
      },
      labelStyle: {
        fill: "#a1a1aa",
        fontSize: 9,
      },
      labelBgStyle: {
        fill: "#090b10",
        fillOpacity: 0.95,
      },
    };
  });

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-[#0D1012]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-200">
            Evidence relationship map
          </h2>
          <p className="mt-1 text-xs text-zinc-500">
            Connections between source media, claims and evidence witnesses
          </p>
        </div>
        <p className="font-mono text-xs text-zinc-500">
          {nodes.length} NODES · {edges.length} EDGES
        </p>
      </div>

      <div className="h-[680px] min-w-[1100px]">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.3}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
        >
          <Background color="#303640" gap={24} size={1} />
          <Controls
            showInteractive={false}
            className="!border-white/10 !bg-[#15191D] !fill-zinc-300"
          />
        </ReactFlow>
      </div>

      <div className="flex flex-wrap gap-5 border-t border-white/10 px-5 py-3 text-[11px] text-zinc-400">
        <span>— Derived from</span>
        <span className="text-[#D96B68]">— Contradicts</span>
        <span className="text-[#69B88A]">— Supports</span>
        <span>┄ Inconclusive</span>
      </div>
    </section>
  );
}
