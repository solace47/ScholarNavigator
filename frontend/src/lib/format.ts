import type { PaperIdentifiers } from "@/types/api";

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function formatScore(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatSeconds(value: number): string {
  return `${value.toFixed(1)}s`;
}

export function identifierEntries(identifiers: PaperIdentifiers): Array<[string, string]> {
  const labels: Record<keyof PaperIdentifiers, string> = {
    doi: "DOI",
    arxiv_id: "arXiv",
    semantic_scholar_id: "S2",
    openalex_id: "OpenAlex",
    pubmed_id: "PubMed",
  };

  return Object.entries(identifiers)
    .filter((entry): entry is [keyof PaperIdentifiers, string] => Boolean(entry[1]))
    .map(([key, value]) => [labels[key], value]);
}
