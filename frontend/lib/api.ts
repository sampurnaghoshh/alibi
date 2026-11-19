
import type { Investigation } from "./types";
import fixture from "../../shared/fixtures/sample_investigation.json";

// Where the ALIBI API lives. Deployed (Docker / Hugging Face) the frontend is served by the API itself,
// so NEXT_PUBLIC_API_URL is set to "" at build time and every call is same-origin ("/api/...").
// For `npm run dev` it is unset and falls back to the local backend on port 8000.
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

/** What GET /api/investigations/{id} said: a finished run, still running (202), not found, or an error. */
export type InvestigationState =
  | { status: "done"; investigation: Investigation }
  | { status: "running" }
  | { status: "missing"; message: string }
  | { status: "error"; message: string };

export async function getInvestigationState(id: string): Promise<InvestigationState> {
  let response: Response;
  try {
    response = await fetch(
      `${API_URL}/api/investigations/${encodeURIComponent(id)}`,
      { cache: "no-store" }
    );
  } catch {
    if (id === fixtureInvestigation.id) {
      return { status: "done", investigation: fixtureInvestigation };
    }
    return { status: "error", message: `The ALIBI API at ${API_URL} could not be reached.` };
  }

  if (response.status === 202) return { status: "running" };   // async run still in progress
  if (response.ok) {
    return { status: "done", investigation: (await response.json()) as Investigation };
  }
  if (response.status === 404) {
    if (id === fixtureInvestigation.id) {
      return { status: "done", investigation: fixtureInvestigation };
    }
    return { status: "missing", message: await readError(response) };
  }
  return { status: "error", message: await readError(response) };
}

const fixtureInvestigation = fixture as Investigation;

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export async function getInvestigation(
  id: string
): Promise<Investigation> {
  try {
    const response = await fetch(
      `${API_URL}/api/investigations/${encodeURIComponent(id)}`,
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(await readError(response));
    }

    return (await response.json()) as Investigation;
  } catch (error) {
    if (id === fixtureInvestigation.id) {
      try {
        const response = await fetch(`${API_URL}/api/fixture`, {
          cache: "no-store",
        });

        if (response.ok) {
          return (await response.json()) as Investigation;
        }
      } catch {
        // Use the local fixture below.
      }

      return fixtureInvestigation;
    }

    throw error;
  }
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

  const response = await fetch(`${API_URL}/api/investigations`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

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
  const response = await fetch(`${API_URL}/api/demo`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return (await response.json()) as DemoCase[];
}

export async function runDemoCase(
  caseName: string
): Promise<Investigation> {
  const response = await fetch(
    `${API_URL}/api/demo/${encodeURIComponent(caseName)}`,
    { method: "POST" }
  );

  if (!response.ok) {
    throw new Error(await readError(response));
  }

  return (await response.json()) as Investigation;
}

export function getMediaUrl(
  investigationId: string,
  storedPath: string
): string {
  const safePath = storedPath
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");

  return `${API_URL}/api/files/${encodeURIComponent(investigationId)}/${safePath}`;
}
