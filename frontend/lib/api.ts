
import type { Investigation } from "./types";
import fixture from "../../shared/fixtures/sample_investigation.json";

const API_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

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

  files.forEach((file) => formData.append("files[]", file));
  formData.append("caption", caption);
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
