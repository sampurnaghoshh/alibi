
export type FindingStatus =
  | "consistent"
  | "contradiction"
  | "inconclusive"
  | "insufficient_signal"
  | "not_applicable";

export type Reliability = "high" | "medium" | "low";

export interface ClaimSource {
  field: "caption";
  start: number;
  end: number;
  text: string;
}

export interface LocationNormalized {
  lat: number;
  lon: number;
  country?: string | null;
  [key: string]: unknown;
}

export interface TimeNormalized {
  date: string;
  start: string;
  end: string;
  precision: "hour" | "part_of_day" | "day" | "week" | "month" | "year";
}

export interface ConditionNormalized {
  condition: "flood" | "heavy_rain" | "rain" | "dry" | "storm" | "other";
}

export type ClaimNormalized =
  | LocationNormalized
  | TimeNormalized
  | ConditionNormalized
  | Record<string, unknown>
  | null;

export interface Claim {
  id: string;
  type: "location" | "time" | "event" | "weather_condition" | "speaker" | "other";
  value: string;
  normalized: ClaimNormalized;
  source: ClaimSource;
  extracted_by: "llm" | "rules";
}

export interface Evidence {
  kind: "api" | "artifact" | "metadata_field" | "archive_match" | "input";
  ref: string;
  label: string;
}

export interface TimestampLocation {
  kind: "timestamp";
  file_id: string;
  start_s: number;
  end_s: number;
}

export interface RegionLocation {
  kind: "region";
  file_id: string;
  frame_s: number | null;
  bbox: [number, number, number, number];
}

export type FindingLocation = TimestampLocation | RegionLocation;

export interface Finding {
  id: string;
  witness: "metadata" | "weather" | "prior_appearance" | "lipsync" | "enf" | "ela";
  family: "context" | "signal" | "pixels";
  claim_ids: string[];
  status: FindingStatus;
  reliability: Reliability;
  title: string;
  summary: string;
  details: Record<string, unknown>;
  evidence: Evidence[];
  locations: FindingLocation[];
  alternatives: string[];
  limitations: string[];
  elapsed_ms: number;
}

export interface StatusCounts {
  consistent: number;
  contradiction: number;
  inconclusive: number;
  insufficient_signal: number;
  not_applicable: number;
}

export type FamilyStatus = "consistent" | "contradiction" | "inconclusive" | "not_applicable";

export interface FamilyStatuses {
  context: FamilyStatus;
  signal: FamilyStatus;
  pixels: FamilyStatus;
}

export interface ExperimentalScore {
  value: number;
  label: string;
}

export interface Assessment {
  headline:
    | "context_not_supported"
    | "signals_inconsistent"
    | "partially_supported"
    | "consistent_so_far"
    | "insufficient_evidence";
  headline_text: string;
  counts: StatusCounts;
  families: FamilyStatuses;
  weak_indicators: string[];
  human_review: "priority" | "standard";
  experimental_score: ExperimentalScore | null;
}

export interface InputFile {
  file_id: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
  duration_s: number | null;
  stored_path: string;
}

export interface GraphNode {
  id: string;
  type: "input" | "claim" | "witness";
  label: string;
  status: FindingStatus | null;
  meta: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: "supports" | "contradicts" | "inconclusive" | "derived_from";
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface HistoryEvent {
  ts: string;
  event: string;
  detail: string;
}

export interface Investigation {
  id: string;
  created_at: string;
  posted_at: string;
  parent_id?: string | null;
  caption: string;
  inputs: InputFile[];
  claims: Claim[];
  findings: Finding[];
  assessment: Assessment;
  graph: Graph;
  history: HistoryEvent[];
}

export interface InvestigationSummary {
  id: string;
  created_at: string;
  parent_id: string | null;
  caption: string;
  inputs: string[];
  headline: Assessment["headline"];
  headline_text: string;
  counts: StatusCounts;
  human_review: "priority" | "standard";
}
