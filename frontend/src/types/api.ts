export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type RunProfile = "fast" | "balanced" | "high_recall" | "evaluation";

export type RelevanceCategory =
  | "highly_relevant"
  | "partially_relevant"
  | "weakly_relevant"
  | "irrelevant"
  | "insufficient_evidence";

export interface HealthResponse {
  status: string;
  version: string;
  time: string;
}

export interface RuntimeConfigResponse {
  mode: string;
  llm: {
    provider: string;
    model: string;
    available: boolean;
  };
  connectors: Array<{
    name: string;
    available: boolean;
    requires_key: boolean;
    reason?: string | null;
  }>;
  limits: {
    max_top_k: number;
    max_search_rounds: number;
    max_candidate_papers: number;
    max_latency_seconds: number;
  };
  features: {
    query_evolution: boolean;
    refchain: boolean;
    evaluation: boolean;
    sse: boolean;
  };
}

export interface SearchRunCreateRequest {
  query: string;
  locale?: string;
  constraints?: {
    time_range?: {
      start_year?: number | null;
      end_year?: number | null;
    } | null;
    venues?: string[];
    must_have_terms?: string[];
    excluded_terms?: string[];
    datasets?: string[];
    paper_types?: string[];
  };
  source_preferences?: string[];
  run_profile?: RunProfile;
  top_k?: number;
  budgets?: {
    max_search_rounds?: number;
    max_candidate_papers?: number;
    max_llm_calls?: number;
    max_total_tokens?: number;
    max_latency_seconds?: number;
  };
  options?: {
    enable_query_evolution?: boolean;
    enable_refchain?: boolean;
    refchain_depth?: number;
    return_markdown?: boolean;
    return_json?: boolean;
    stream_events?: boolean;
  };
}

export interface InternalSearchPreviewRequest {
  query: string;
  top_k?: number;
  run_profile?: RunProfile;
  enable_refchain?: boolean;
  enable_query_evolution?: boolean;
  current_year?: number | null;
}

export interface SearchRunCreateResponse {
  run_id: string;
  status: RunStatus;
  created_at: string;
  links: {
    self: string;
    events: string;
    result: string;
  };
}

export interface CostReport {
  api_call_count: number;
  search_api_call_count: number;
  llm_call_count: number;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  estimated_total_tokens: number;
  latency_seconds: number;
  cache_hit_count: number;
  search_rounds: number;
  judged_paper_count: number;
}

export interface SearchRunStatusResponse {
  run_id: string;
  status: RunStatus;
  current_stage: string;
  progress: {
    completed_stages: string[];
    candidate_paper_count: number;
    judged_paper_count: number;
  };
  cost_report: CostReport;
  created_at: string;
  updated_at: string;
}

export interface PaperIdentifiers {
  doi?: string | null;
  arxiv_id?: string | null;
  semantic_scholar_id?: string | null;
  openalex_id?: string | null;
  pubmed_id?: string | null;
}

export interface PaperUrls {
  landing_page?: string | null;
  pdf?: string | null;
}

export interface Paper {
  title: string;
  authors: string[];
  year: number;
  venue?: string | null;
  abstract: string;
  identifiers: PaperIdentifiers;
  urls: PaperUrls;
  sources: string[];
}

export interface EvidenceItem {
  source: string;
  text: string;
  confidence: number;
}

export interface RankedPaper {
  rank: number;
  paper: Paper;
  relevance_score: number;
  category: RelevanceCategory;
  matched_constraints: string[];
  ranking_reason: string;
  evidence: EvidenceItem[];
}

export interface QueryAnalysis {
  intent_type: string;
  domain: string;
  research_topics: string[];
  constraints: Record<string, unknown>;
}

export interface SearchPlan {
  expanded_queries: string[];
  source_preferences: string[];
  max_rounds: number;
}

export interface MethodCluster {
  name: string;
  paper_ranks: number[];
  summary: string;
}

export interface TimelineItem {
  year: number;
  paper_ranks: number[];
  summary: string;
}

export interface CitationGraph {
  nodes: Array<{
    id: string;
    label: string;
    rank?: number | null;
  }>;
  edges: Array<{
    source: string;
    target: string;
    relation: string;
  }>;
}

export interface SearchRunResultResponse {
  run_id: string;
  status: RunStatus;
  partial: boolean;
  query_analysis: QueryAnalysis;
  search_plan: SearchPlan;
  highly_relevant_papers: RankedPaper[];
  partially_relevant_papers: RankedPaper[];
  method_clusters: MethodCluster[];
  timeline: TimelineItem[];
  citation_graph: CitationGraph;
  missing_evidence: string[];
  cost_report: CostReport;
}

export interface StreamEvent {
  event: string;
  payload: Record<string, unknown>;
  receivedAt: string;
}
