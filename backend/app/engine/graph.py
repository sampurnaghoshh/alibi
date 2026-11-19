"""
Contradiction graph for React Flow (CLAUDE.md §4.5). Owner: Sampurna.

Nodes: inputs, then claims, then one witness node per finding.
Edges: input -> claim ("derived_from"), finding -> claim (by finding status).
Findings with no claims point at the input they analysed. not_applicable findings get no edges.
"""

from __future__ import annotations

from typing import Optional

from app import config
from app.schemas import Claim, EdgeRelation, Finding, Graph, GraphEdge, GraphNode, InputFile, Status

RELATION: dict[str, EdgeRelation] = {
    "contradiction": "contradicts",
    "consistent": "supports",
    "inconclusive": "inconclusive",
    "insufficient_signal": "inconclusive",
}

# Claim node status = worst status among findings pointing at it.
_SEVERITY = {"contradiction": 3, "inconclusive": 2, "consistent": 1}
_CLAIM_STATUS: dict[str, Status] = {
    "contradiction": "contradiction",
    "inconclusive": "inconclusive",
    "insufficient_signal": "inconclusive",
    "consistent": "consistent",
}


def _claim_label(c: Claim) -> str:
    return c.value[:1].upper() + c.value[1:]


def _analysed_input(f: Finding, inputs: list[InputFile]) -> Optional[str]:
    """The file a claim-less finding is about: its first location, else the first input."""
    if f.locations:
        return f.locations[0].file_id
    return inputs[0].file_id if inputs else None


def build_graph(inputs: list[InputFile], claims: list[Claim], findings: list[Finding]) -> Graph:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    def edge(source: str, target: str, relation: EdgeRelation) -> None:
        edges.append(GraphEdge(id=f"e{len(edges) + 1}", source=source, target=target, relation=relation))

    # Inputs and claims. Claims come from the caption that accompanies every input.
    for i in inputs:
        nodes.append(GraphNode(id=i.file_id, type="input", label=i.name, status=None, meta={"mime": i.mime}))
    claim_nodes = {
        c.id: GraphNode(id=c.id, type="claim", label=_claim_label(c), status=None, meta={"claim_type": c.type})
        for c in claims
    }
    nodes.extend(claim_nodes.values())
    for i in inputs:
        for c in claims:
            edge(i.file_id, c.id, "derived_from")

    # One witness node per finding, edges by status.
    for f in findings:
        nodes.append(GraphNode(
            id=f.id,
            type="witness",
            label=config.WITNESS_LABELS.get(f.witness, f.witness),
            status=f.status,
            meta={"family": f.family, "reliability": f.reliability},
        ))
        relation = RELATION.get(f.status)
        if relation is None:  # not_applicable: node only
            continue
        if f.claim_ids:
            for cid in f.claim_ids:
                edge(f.id, cid, relation)
                node = claim_nodes[cid]
                new = _CLAIM_STATUS[f.status]
                if node.status is None or _SEVERITY[new] > _SEVERITY[node.status]:
                    node.status = new
        elif (target := _analysed_input(f, inputs)) is not None:
            edge(f.id, target, relation)

    return Graph(nodes=nodes, edges=edges)
