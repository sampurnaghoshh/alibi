import type { Investigation } from "./types";

// Where the ALIBI API lives, fixed at build time:
// - Vercel: NEXT_PUBLIC_API_URL = the backend's public URL (the laptop's Cloudflare tunnel, https://....trycloudflare.com).
// - Same-origin builds (the API serves frontend/out, e.g. Docker): NEXT_PUBLIC_API_URL="" -> "/api/...".
// - `npm run dev`: unset -> the local backend on port 8000.
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

/** The Live Desk big screen is served by the backend. */
export const LIVE_DESK_URL = `${API_URL}/live`;

/** Shown instead of an error whenever the analysis server cannot be reached. */
export const OFFLINE_MESSAGE =
  "The analysis server is offline right now. The live demo runs on our forensic workstation.";

/** The API could not be reached: no connection, or the tunnel answered for a server that is down. */
export class ApiOffline extends Error {
  constructor() {
    super(OFFLINE_MESSAGE);
    this.name = "ApiOffline";
  }
}

const GATEWAY_DOWN = new Set([502, 503, 504, 520, 521, 522, 523, 524, 530]);

/** fetch() against the API; network failures and tunnel/gateway errors become ApiOffline. */
async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiOffline();
  }
  // A Cloudflare tunnel whose laptop is off answers with its own HTML error page.
  const html = (response.headers.get("content-type") || "").includes("text/html");
  if (GATEWAY_DOWN.has(response.status) || (!response.ok && html)) {
    throw new ApiOffline();
  }
  return response;
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

/** True when GET /api/health answers within a few seconds. */
export async function isApiOnline(timeoutMs = 6000): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await apiFetch("/api/health", { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** What GET /api/investigations/{id} said. */
export type InvestigationState =
  | { status: "done"; investigation: Investigation }
  | { status: "running" }
  | { status: "missing"; message: string }
  | { status: "offline" }
  | { status: "error"; message: string };

export async function getInvestigationState(id: string): Promise<InvestigationState> {
  let response: Response;
  try {
    response = await apiFetch(`/api/investigations/${encodeURIComponent(id)}`);
  } catch {
    return { status: "offline" };
  }
  if (response.status === 202) return { status: "running" };   // async run still in progress
  if (response.ok) {
    return { status: "done", investigation: (await response.json()) as Investigation };
  }
  if (response.status === 404) return { status: "missing", message: await readError(response) };
  return { status: "error", message: await readError(response) };
}

export async function uploadInvestigation(
  files: File[],
  caption: string,
  postedAt: string
): Promise<Investigation> {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));    // one "files" part per file
  formData.append("caption", caption);
  // A plain date ("2026-11-19") lets the backend resolve "yesterday evening" in IST; an ISO string
  // with "Z" would move every relative time to UTC.
  formData.append("posted_at", postedAt);

  const response = await apiFetch("/api/investigations", { method: "POST", body: formData });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as Investigation;
}

export interface DemoCase {
  name: string;
  caption: string;
  posted_at: string;
  files: string[];
  expect: unknown;
  ready: boolean;
  problem: string | null;
}

export async function getDemoCases(): Promise<DemoCase[]> {
  const response = await apiFetch("/api/demo");
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as DemoCase[];
}

export async function runDemoCase(caseName: string): Promise<Investigation> {
  const response = await apiFetch(`/api/demo/${encodeURIComponent(caseName)}`, { method: "POST" });
  if (!response.ok) throw new Error(await readError(response));
  return (await response.json()) as Investigation;
}

export function getMediaUrl(investigationId: string, storedPath: string): string {
  const safePath = storedPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `${API_URL}/api/files/${encodeURIComponent(investigationId)}/${safePath}`;
}

/** The plain AI & manipulation answer + summary sentence (app/engine/summary.py), same text as the Live Desk. */
export interface AiCheck {
  question: string; answer: string; kind: "ai_marker" | "synthetic_strong" | "editing" | "synthetic_weak" | "none"; note: string
  alert?: boolean         // show in the contradiction colour
  badge?: string          // "AI: DETECTED" | "AI SIGNALS: STRONG" | "EDITED: DETECTED" | "AI SIGNALS: WEAK" | "AI: NOT DETECTED"
  badge_detail?: string   // the generator / software, or the "not proof" sentence
}
export interface Summary { sentence: string; ai_check?: AiCheck; caption_less?: boolean }

export async function getSummary(id: string): Promise<Summary | null> {
  try {
    const response = await apiFetch(`/api/investigations/${encodeURIComponent(id)}/summary`);
    return response.ok ? ((await response.json()) as Summary) : null;
  } catch {
    return null;
  }
}
