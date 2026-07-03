"use client";

import {
  Activity,
  AlertTriangle,
  BookOpenCheck,
  Brain,
  CheckCircle2,
  Clock3,
  Database,
  Download,
  ExternalLink,
  FileText,
  GitBranch,
  Moon,
  Network,
  Send,
  RefreshCw,
  Search,
  Server,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Timer,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  cancelRealSearchRun,
  createRealSearchRun,
  getHealth,
  getRealSearchRun,
  getRealSearchRunResult,
  getRuntimeConfig,
  streamRealSearchRunEvents,
} from "@/lib/api";
import { exportSearchResultAsJson, exportSearchResultAsMarkdown } from "@/lib/export";
import { formatNumber, formatScore, formatSeconds, identifierEntries } from "@/lib/format";
import type {
  CostReport,
  RankedPaper,
  RunProfile,
  RuntimeConfigResponse,
  SearchRunCreateRequest,
  SearchRunResultResponse,
  SearchRunStatusResponse,
  StreamEvent,
  SynthesisOutput,
} from "@/types/api";
import { Badge, Button, FieldLabel, SectionPanel, SkeletonLine, TextInput } from "./ui";

const EXAMPLES = [
  "请帮我搜索 2020 年以来关于 LLM reranking 在学术论文检索中的代表性论文，重点关注 ACL、EMNLP、SIGIR。",
  "查找评测科研文献搜索智能体的 benchmark 论文，重点关注召回率、准确率、F1 和端到端延迟。",
  "搜索使用 citation graph 或 reference chain 扩展来提升论文推荐召回率的研究，并说明代表性方法路线。",
];

const STAGES = [
  {
    key: "query_understanding",
    title: "理解查询",
    icon: Brain,
  },
  {
    key: "retrieval",
    title: "检索候选",
    icon: Database,
  },
  {
    key: "judgement",
    title: "相关性判断",
    icon: BookOpenCheck,
  },
  {
    key: "reranking",
    title: "重排序",
    icon: GitBranch,
  },
  {
    key: "synthesis",
    title: "证据归纳",
    icon: Sparkles,
  },
];

const PROFILE_LABELS: Record<RunProfile, string> = {
  fast: "快速",
  balanced: "均衡",
  high_recall: "高召回",
  evaluation: "评测",
};

type SourceMode =
  | "recommended"
  | "arxiv"
  | "semantic_scholar"
  | "pubmed"
  | "openalex"
  | "all";
type ThemeMode = "dark" | "light";
type StageLatencyItem = {
  stage: string;
  label: string;
  seconds: number;
};
type RunConfigSnapshot = {
  sourcePreferences: string[];
  runProfile: RunProfile;
  topK: number;
  enableQueryEvolution: boolean;
  enableRefchain: boolean;
  enableLlmQueryUnderstanding: boolean;
  enableLlmJudgement: boolean;
};

const SOURCE_MODE_LABELS: Record<SourceMode, string> = {
  recommended: "推荐",
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
  pubmed: "PubMed",
  openalex: "OpenAlex",
  all: "全部",
};

const SOURCE_MODE_DESCRIPTIONS: Record<SourceMode, string> = {
  recommended: "arXiv + Semantic Scholar，兼顾稳定性和覆盖",
  arxiv: "更稳定更快",
  semantic_scholar: "提升召回，可能限流",
  pubmed: "生物医学文献，适合临床/医疗查询",
  openalex: "覆盖更广，外部服务波动较多",
  all: "覆盖最大，但 OpenAlex 可能不稳定",
};

const STAGE_LATENCY_LABELS: Record<string, string> = {
  query_understanding: "查询理解",
  retrieval: "候选检索",
  judgement: "相关性判断",
  reranking: "重排序",
  query_evolution: "查询演化",
  refchain: "RefChain",
  synthesis: "证据归纳",
};

const STAGE_LATENCY_ORDER = [
  "query_understanding",
  "retrieval",
  "judgement",
  "reranking",
  "query_evolution",
  "refchain",
  "synthesis",
];

export function ScholarNavigatorApp() {
  const [theme, setTheme] = useState<ThemeMode>("dark");
  const [query, setQuery] = useState(EXAMPLES[0]);
  const [topK, setTopK] = useState(5);
  const [currentYear, setCurrentYear] = useState(2026);
  const [runProfile, setRunProfile] = useState<RunProfile>("fast");
  const [sourceMode, setSourceMode] = useState<SourceMode>("recommended");
  const [enableRefchain, setEnableRefchain] = useState(false);
  const [enableQueryEvolution, setEnableQueryEvolution] = useState(false);
  const [enableLlmQueryUnderstanding, setEnableLlmQueryUnderstanding] = useState(false);
  const [enableLlmJudgement, setEnableLlmJudgement] = useState(false);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigResponse | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<SearchRunStatusResponse | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [result, setResult] = useState<SearchRunResultResponse | null>(null);
  const [activeRunConfig, setActiveRunConfig] = useState<RunConfigSnapshot | null>(null);
  const eventSourceCleanup = useRef<(() => void) | null>(null);
  const searchSequence = useRef(0);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  useEffect(() => {
    let cancelled = false;

    async function loadRuntime() {
      try {
        await getHealth();
        const config = await getRuntimeConfig();
        if (!cancelled) {
          setRuntimeConfig(config);
          setBackendError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setBackendError("后端服务不可用，请先启动后端真实检索服务。");
        }
      }
    }

    loadRuntime();
    return () => {
      cancelled = true;
      searchSequence.current += 1;
      eventSourceCleanup.current?.();
    };
  }, []);

  async function handleSearch() {
    if (!query.trim()) {
      setFormError("请输入学术查询。");
      return;
    }

    eventSourceCleanup.current?.();
    const sequence = searchSequence.current + 1;
    searchSequence.current = sequence;
    setFormError(null);
    setBackendError(null);
    setIsSubmitting(true);
    setIsCancelling(false);
    setRunId(null);
    setStatus(null);
    setEvents([]);
    setResult(null);
    const runConfigSnapshot: RunConfigSnapshot = {
      sourcePreferences: sourcePreferencesForMode(sourceMode),
      runProfile,
      topK,
      enableQueryEvolution,
      enableRefchain,
      enableLlmQueryUnderstanding,
      enableLlmJudgement,
    };
    setActiveRunConfig(runConfigSnapshot);

    try {
      const created = await createRealSearchRun({
        query,
        locale: "zh-CN",
        constraints: {
          time_range: {
            end_year: currentYear,
          },
          venues: [],
          must_have_terms: [],
          excluded_terms: [],
          datasets: [],
          paper_types: [],
        },
        source_preferences: runConfigSnapshot.sourcePreferences,
        run_profile: runConfigSnapshot.runProfile,
        top_k: runConfigSnapshot.topK,
        budgets: buildBudgets(runConfigSnapshot.runProfile),
        options: {
          enable_query_evolution: runConfigSnapshot.enableQueryEvolution,
          enable_refchain: runConfigSnapshot.enableRefchain,
          enable_llm_query_understanding: runConfigSnapshot.enableLlmQueryUnderstanding,
          enable_llm_judgement: runConfigSnapshot.enableLlmJudgement,
          refchain_depth: runConfigSnapshot.enableRefchain ? 1 : 0,
          return_markdown: true,
          return_json: true,
          stream_events: true,
        },
      });

      setRunId(created.run_id);
      setStatus(buildInitialRealStatus(created.run_id, created.status));
      eventSourceCleanup.current = streamRealSearchRunEvents(
        created.run_id,
        (event) => {
          if (searchSequence.current !== sequence) {
            return;
          }
          setEvents((current) => [...current, event]);
        },
        (message) => {
          if (searchSequence.current !== sequence) {
            return;
          }
          setEvents((current) => [
            ...current,
            {
              event: "sse_error",
              payload: { message },
              receivedAt: new Date().toISOString(),
            },
          ]);
        },
      );

      await pollRealSearchRun(created.run_id, sequence);
    } catch (error) {
      if (searchSequence.current === sequence) {
        setBackendError(
          error instanceof Error
            ? error.message
            : "后端服务不可用，请先启动后端真实检索服务。",
        );
        setIsSubmitting(false);
      }
    }
  }

  async function pollRealSearchRun(runId: string, sequence: number) {
    const pollIntervalMs = 800;
    try {
      while (searchSequence.current === sequence) {
        const runStatus = await getRealSearchRun(runId);
        if (searchSequence.current !== sequence) {
          return;
        }
        setStatus(runStatus);

        if (runStatus.status === "failed") {
          let message = "真实检索失败";
          try {
            await getRealSearchRunResult(runId);
          } catch (error) {
            message = error instanceof Error ? error.message : message;
          }
          if (searchSequence.current === sequence) {
            setBackendError(message);
          }
          return;
        }

        if (runStatus.status === "cancelled") {
          return;
        }

        if (runStatus.status === "succeeded") {
          while (searchSequence.current === sequence) {
            try {
              const runResult = await getRealSearchRunResult(runId);
              if (searchSequence.current === sequence) {
                setResult(runResult);
              }
              return;
            } catch (error) {
              if (error instanceof ApiError && error.status === 409) {
                await sleep(pollIntervalMs);
                continue;
              }
              throw error;
            }
          }
          return;
        }

        await sleep(pollIntervalMs);
      }
    } finally {
      if (searchSequence.current === sequence) {
        setIsSubmitting(false);
      }
    }
  }

  async function handleCancelRealSearch() {
    if (!runId) {
      return;
    }

    setIsCancelling(true);
    setBackendError(null);
    try {
      const cancelledStatus = await cancelRealSearchRun(runId);
      searchSequence.current += 1;
      eventSourceCleanup.current?.();
      setStatus(cancelledStatus);
      setIsSubmitting(false);
    } catch (error) {
      setBackendError(
        error instanceof Error
          ? error.message
          : "取消真实检索失败，请稍后重试。",
      );
    } finally {
      setIsCancelling(false);
    }
  }

  const costReport = status?.cost_report ?? result?.cost_report ?? null;

  return (
    <main className="app-shell">
      <div className="workspace space-y-6">
        <Header
          theme={theme}
          onThemeChange={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
        />

        {backendError ? <BackendWarning message={backendError} /> : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(380px,0.9fr)_minmax(0,1.4fr)]">
          <SearchWorkbench
            query={query}
            topK={topK}
            currentYear={currentYear}
            runProfile={runProfile}
            sourceMode={sourceMode}
            enableRefchain={enableRefchain}
            enableQueryEvolution={enableQueryEvolution}
            enableLlmQueryUnderstanding={enableLlmQueryUnderstanding}
            enableLlmJudgement={enableLlmJudgement}
            isSubmitting={isSubmitting}
            formError={formError}
            onQueryChange={setQuery}
            onTopKChange={setTopK}
            onCurrentYearChange={setCurrentYear}
            onRunProfileChange={setRunProfile}
            onSourceModeChange={setSourceMode}
            onRefchainChange={setEnableRefchain}
            onQueryEvolutionChange={setEnableQueryEvolution}
            onLlmQueryUnderstandingChange={setEnableLlmQueryUnderstanding}
            onLlmJudgementChange={setEnableLlmJudgement}
            onSearch={handleSearch}
          />

          <RunProgress
            runId={runId}
            status={status}
            events={events}
            costReport={costReport}
            runConfig={activeRunConfig}
            isSubmitting={isSubmitting}
            isCancelling={isCancelling}
            onCancelRealSearch={handleCancelRealSearch}
          />
        </div>

        <ResultsPanel result={result} isLoading={isSubmitting && !result} />
      </div>
    </main>
  );
}

function buildInitialRealStatus(
  runId: string,
  status: SearchRunStatusResponse["status"],
): SearchRunStatusResponse {
  const now = new Date().toISOString();
  return {
    run_id: runId,
    status,
    current_stage: status,
    progress: {
      completed_stages: [],
      candidate_paper_count: 0,
      judged_paper_count: 0,
    },
    cost_report: emptyCostReport(),
    created_at: now,
    updated_at: now,
  };
}

function emptyCostReport(): CostReport {
  return {
    api_call_count: 0,
    search_api_call_count: 0,
    llm_call_count: 0,
    llm_prompt_tokens: 0,
    llm_completion_tokens: 0,
    llm_total_tokens: 0,
    estimated_input_tokens: 0,
    estimated_output_tokens: 0,
    estimated_total_tokens: 0,
    latency_seconds: 0,
    cache_hit_count: 0,
    search_rounds: 0,
    judged_paper_count: 0,
  };
}

function buildBudgets(runProfile: RunProfile): SearchRunCreateRequest["budgets"] {
  return {
    max_search_rounds: runProfile === "fast" ? 1 : 2,
    max_candidate_papers: runProfile === "high_recall" ? 300 : 200,
    max_llm_calls: 0,
    max_total_tokens: 0,
    max_latency_seconds: runProfile === "fast" ? 45 : 90,
  };
}

function sourcePreferencesForMode(sourceMode: SourceMode): string[] {
  if (sourceMode === "recommended") {
    return ["arxiv", "semantic_scholar"];
  }
  if (sourceMode === "arxiv") {
    return ["arxiv"];
  }
  if (sourceMode === "semantic_scholar") {
    return ["semantic_scholar"];
  }
  if (sourceMode === "pubmed") {
    return ["pubmed"];
  }
  if (sourceMode === "openalex") {
    return ["openalex"];
  }
  return ["openalex", "arxiv", "semantic_scholar"];
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function Header({
  theme,
  onThemeChange,
}: {
  theme: ThemeMode;
  onThemeChange: () => void;
}) {
  return (
    <header className="hero-panel overflow-hidden rounded-lg px-5 py-6 md:px-8 lg:px-10">
      <div className="relative z-10 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-4xl font-black leading-tight md:text-6xl">ScholarNavigator</h1>
          <p className="mt-3 max-w-3xl text-base leading-7 text-[var(--muted-strong)] md:text-xl">
            面向科研场景下复杂学术查询的智能论文搜索与推荐。
          </p>
        </div>
        <Button
          type="button"
          variant="secondary"
          onClick={onThemeChange}
          aria-label={theme === "dark" ? "切换到浅色模式" : "切换到深色模式"}
        >
          {theme === "dark" ? (
            <Sun className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Moon className="h-4 w-4" aria-hidden="true" />
          )}
          {theme === "dark" ? "浅色" : "深色"}
        </Button>
      </div>
    </header>
  );
}

function BackendWarning({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-[color-mix(in_srgb,var(--danger)_55%,var(--border))] bg-[color-mix(in_srgb,var(--danger)_12%,var(--surface))] p-4 text-sm text-[var(--foreground)]"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--danger)]" aria-hidden="true" />
        <div>
          <p className="font-semibold">{message}</p>
          <p className="mt-1 text-[var(--muted)]">
            默认地址为 http://localhost:8000，可通过 NEXT_PUBLIC_API_BASE_URL 调整。
          </p>
        </div>
      </div>
    </div>
  );
}

function SearchWorkbench({
  query,
  topK,
  currentYear,
  runProfile,
  sourceMode,
  enableRefchain,
  enableQueryEvolution,
  enableLlmQueryUnderstanding,
  enableLlmJudgement,
  isSubmitting,
  formError,
  onQueryChange,
  onTopKChange,
  onCurrentYearChange,
  onRunProfileChange,
  onSourceModeChange,
  onRefchainChange,
  onQueryEvolutionChange,
  onLlmQueryUnderstandingChange,
  onLlmJudgementChange,
  onSearch,
}: {
  query: string;
  topK: number;
  currentYear: number;
  runProfile: RunProfile;
  sourceMode: SourceMode;
  enableRefchain: boolean;
  enableQueryEvolution: boolean;
  enableLlmQueryUnderstanding: boolean;
  enableLlmJudgement: boolean;
  isSubmitting: boolean;
  formError: string | null;
  onQueryChange: (value: string) => void;
  onTopKChange: (value: number) => void;
  onCurrentYearChange: (value: number) => void;
  onRunProfileChange: (value: RunProfile) => void;
  onSourceModeChange: (value: SourceMode) => void;
  onRefchainChange: (value: boolean) => void;
  onQueryEvolutionChange: (value: boolean) => void;
  onLlmQueryUnderstandingChange: (value: boolean) => void;
  onLlmJudgementChange: (value: boolean) => void;
  onSearch: () => void;
}) {
  return (
    <SectionPanel aria-labelledby="search-workbench-title" className="h-fit rounded-lg">
      <div className="space-y-6">
        <div className="ow-search">
          <label id="search-workbench-title" className="ow-search__label" htmlFor="query">
            学术检索
          </label>
          <div className="ow-search__field">
            <svg className="ow-search__icon" viewBox="0 0 256 256" aria-hidden="true">
              <path d="M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z" />
            </svg>
            <textarea
              id="query"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              className="ow-search__input"
              placeholder="输入复杂学术查询..."
            />
            <button
              type="button"
              className="ow-search__send"
              onClick={onSearch}
              disabled={isSubmitting}
              aria-label="发送检索请求"
            >
              {isSubmitting ? (
                <RefreshCw className="h-5 w-5 motion-safe:animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-6 w-6" aria-hidden="true" />
              )}
            </button>
          </div>
          {formError ? <p className="mt-2 px-2 text-sm text-[var(--danger)]">{formError}</p> : null}
        </div>

        <div>
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <h3 className="font-bold">简洁配置</h3>
              <p className="mt-1 text-sm text-[var(--muted)]">数据源、返回数量与运行模式</p>
            </div>
          </div>
          <div role="radiogroup" aria-label="选择检索数据源" className="radio-inputs">
            {(Object.keys(SOURCE_MODE_LABELS) as SourceMode[]).map((mode) => {
              const selected = sourceMode === mode;
              return (
                <label
                  key={mode}
                  className="radio"
                >
                  <input
                    type="radio"
                    name="source-mode"
                    value={mode}
                    checked={selected}
                    onChange={() => onSourceModeChange(mode)}
                  />
                  <span className="name">
                    <span>
                      <span className="block text-sm font-bold text-[var(--foreground)]">
                        {SOURCE_MODE_LABELS[mode]}
                      </span>
                      <span className="mt-1 block text-xs leading-5 text-[var(--muted)]">
                        {SOURCE_MODE_DESCRIPTIONS[mode]}
                      </span>
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <FieldLabel htmlFor="top-k">返回数量</FieldLabel>
            <TextInput
              id="top-k"
              type="number"
              min={1}
              max={100}
              value={topK}
              onChange={(event) => onTopKChange(Number(event.target.value))}
              className="rounded-lg"
            />
          </div>
          <div>
            <FieldLabel htmlFor="run-profile">运行模式</FieldLabel>
            <select
              id="run-profile"
              value={runProfile}
              onChange={(event) => onRunProfileChange(event.target.value as RunProfile)}
              className="control w-full rounded-lg px-4 py-2 text-sm"
            >
              {(Object.keys(PROFILE_LABELS) as RunProfile[]).map((profile) => (
                <option key={profile} value={profile}>
                  {PROFILE_LABELS[profile]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <details className="details-card rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-4">
          <summary className="cursor-pointer text-sm font-bold text-[var(--foreground)]">
            高级选项
          </summary>
          <div className="mt-4 space-y-4">
            <div>
              <FieldLabel htmlFor="current-year">当前年份</FieldLabel>
              <TextInput
                id="current-year"
                type="number"
                min={1900}
                max={2100}
                value={currentYear}
                onChange={(event) => onCurrentYearChange(Number(event.target.value))}
                className="rounded-lg"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <ToggleControl
                label="RefChain 引用扩展"
                description="沿高相关论文做单层引用扩展"
                checked={enableRefchain}
                onChange={onRefchainChange}
              />
              <ToggleControl
                label="查询演化"
                description="基于初始结果生成补充检索式"
                checked={enableQueryEvolution}
                onChange={onQueryEvolutionChange}
              />
              <ToggleControl
                label="LLM 查询理解"
                description="增强查询解析，但会增加延迟"
                checked={enableLlmQueryUnderstanding}
                onChange={onLlmQueryUnderstandingChange}
              />
              <ToggleControl
                label="LLM 相关性判断"
                description="判断更强，但成本和延迟更高"
                checked={enableLlmJudgement}
                onChange={onLlmJudgementChange}
              />
            </div>
          </div>
        </details>

        <div>
          <p className="mb-2 text-sm font-semibold text-[var(--muted-strong)]">示例查询</p>
          <div className="grid gap-2">
            {EXAMPLES.map((example, index) => (
              <button
                key={example}
                type="button"
                onClick={() => onQueryChange(example)}
                className="min-h-12 rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] px-4 py-3 text-left text-sm leading-6 text-[var(--muted-strong)] transition duration-200 hover:border-[var(--primary)] hover:text-[var(--foreground)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
              >
                <span className="mr-2 font-bold text-[var(--primary)]">0{index + 1}</span>
                {example}
              </button>
            ))}
          </div>
        </div>
      </div>
    </SectionPanel>
  );
}

function ToggleControl({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle-card flex min-h-20 cursor-pointer items-center justify-between gap-3 rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3 text-left transition duration-200 hover:border-[var(--primary)]">
      <span>
        <span className="block text-sm font-semibold text-[var(--foreground)]">{label}</span>
        <span className="mt-1 block text-xs text-[var(--muted)]">{description}</span>
      </span>
      <span className="switch">
        <input
          className="toggle"
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="slider" />
        <span className="card-side" />
      </span>
    </label>
  );
}

function RunProgress({
  runId,
  status,
  events,
  costReport,
  runConfig,
  isSubmitting,
  isCancelling,
  onCancelRealSearch,
}: {
  runId: string | null;
  status: SearchRunStatusResponse | null;
  events: StreamEvent[];
  costReport: CostReport | null;
  runConfig: RunConfigSnapshot | null;
  isSubmitting: boolean;
  isCancelling: boolean;
  onCancelRealSearch: () => void;
}) {
  const completedStages = new Set(status?.progress.completed_stages ?? []);
  events.forEach((event) => {
    if (event.event === "stage_completed" && typeof event.payload.stage === "string") {
      completedStages.add(event.payload.stage);
    }
  });
  if (status?.status === "succeeded") {
    completedStages.add("synthesis");
  }
  const canCancelRealSearch =
    Boolean(runId) &&
    Boolean(status && ["queued", "running"].includes(status.status));

  return (
    <SectionPanel aria-labelledby="run-progress-title" className="rounded-lg">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="mb-2 text-sm font-semibold text-[var(--primary)]">任务进度</p>
          <h2 id="run-progress-title" className="text-2xl font-black">
            真实检索运行状态
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {runId ? `任务编号：${runId}` : "等待创建检索任务"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canCancelRealSearch ? (
            <Button
              type="button"
              variant="secondary"
              onClick={onCancelRealSearch}
              disabled={isCancelling}
            >
              {isCancelling ? (
                <RefreshCw className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" />
              ) : (
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              )}
              取消检索
            </Button>
          ) : null}
          <Badge className={isSubmitting ? "text-[var(--warning)]" : "text-[var(--accent)]"}>
            {status ? statusLabel(status.status) : isSubmitting ? "运行中" : "待启动"}
          </Badge>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-5">
        {STAGES.map((stage) => {
          const Icon = stage.icon;
          const done = completedStages.has(stage.key);
          const active = events.some(
            (event) => event.event === "stage_started" && event.payload.stage === stage.key,
          );
          return (
            <div
              key={stage.key}
              className={`rounded-md border p-3 transition duration-200 ${
                done
                  ? "border-[color-mix(in_srgb,var(--accent)_58%,var(--border))] bg-[var(--accent-soft)]"
                  : active
                    ? "border-[var(--primary)] bg-[color-mix(in_srgb,var(--primary)_12%,var(--surface))]"
                    : "border-[var(--border)] bg-[var(--surface-raised)]"
              }`}
            >
              <div className="mb-3 flex items-center justify-between">
                <Icon className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
                {done ? (
                  <CheckCircle2 className="h-4 w-4 text-[var(--accent)]" aria-hidden="true" />
                ) : null}
              </div>
              <p className="text-sm font-semibold">{stage.title}</p>
              <p className="mt-1 text-xs text-[var(--muted)]">
                {done ? "已完成" : active ? "进行中" : "等待中"}
              </p>
            </div>
          );
        })}
      </div>

      {runConfig ? <CompactRunConfig runConfig={runConfig} /> : null}

      <details className="details-card mt-5 rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-4">
        <summary className="cursor-pointer text-sm font-bold text-[var(--foreground)]">
          运行诊断 / 调试信息
        </summary>
        <div className="mt-4 space-y-5">
          <CostMetrics costReport={costReport} />
          <RunConfigSummary runConfig={runConfig} />
          <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
            <div className="panel-soft rounded-lg p-4">
              <div className="mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
                <h3 className="font-semibold">状态摘要</h3>
              </div>
              {status ? (
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <MetricRow label="当前阶段" value={status.current_stage} />
                  <MetricRow label="候选数" value={status.progress.candidate_paper_count} />
                  <MetricRow label="已判断论文" value={status.progress.judged_paper_count} />
                  <MetricRow label="完成阶段数" value={status.progress.completed_stages.length} />
                </dl>
              ) : (
                <EmptyBlock lines={3} />
              )}
            </div>

            <div className="panel-soft rounded-lg p-4">
              <div className="mb-3 flex items-center gap-2">
                <Clock3 className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
                <h3 className="font-semibold">真实检索事件</h3>
              </div>
              {events.length ? (
                <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                  {events.map((event, index) => (
                    <div
                      key={`${event.event}-${index}`}
                      className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge>{eventNameLabel(event.event)}</Badge>
                        {typeof event.payload.stage === "string" ? (
                          <Badge>{String(event.payload.stage)}</Badge>
                        ) : null}
                        {typeof event.payload.connector === "string" ? (
                          <Badge>{String(event.payload.connector)}</Badge>
                        ) : null}
                      </div>
                      <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-[var(--muted)]">
                        {JSON.stringify(event.payload, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyBlock lines={4} />
              )}
            </div>
          </div>
        </div>
      </details>
    </SectionPanel>
  );
}

function RunConfigSummary({ runConfig }: { runConfig: RunConfigSnapshot | null }) {
  if (!runConfig) {
    return null;
  }

  const items = [
    {
      label: "数据源",
      value: runConfig.sourcePreferences.join(" / "),
    },
    {
      label: "运行模式",
      value: PROFILE_LABELS[runConfig.runProfile],
    },
    {
      label: "返回数量",
      value: formatNumber(runConfig.topK),
    },
    {
      label: "查询演化",
      value: formatBoolean(runConfig.enableQueryEvolution),
    },
    {
      label: "RefChain",
      value: formatBoolean(runConfig.enableRefchain),
    },
    {
      label: "LLM 查询理解",
      value: formatBoolean(runConfig.enableLlmQueryUnderstanding),
    },
    {
      label: "LLM 相关性判断",
      value: formatBoolean(runConfig.enableLlmJudgement),
    },
  ];

  return (
    <section
      aria-labelledby="run-config-summary-title"
      className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-4"
    >
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h3 id="run-config-summary-title" className="font-semibold">
              运行配置
            </h3>
          </div>
          <p className="mt-1 text-sm leading-6 text-[var(--muted)]">
            本区域固定展示当前 run 创建时的配置；后续修改左侧表单不会改变该摘要。
          </p>
        </div>
        <Badge>快照</Badge>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {items.map((item) => (
          <div
            key={item.label}
            className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2"
          >
            <dt className="break-words text-xs font-semibold uppercase text-[var(--muted)]">
              {item.label}
            </dt>
            <dd className="mt-1 break-words text-sm font-semibold text-[var(--foreground)]">
              {item.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function CompactRunConfig({ runConfig }: { runConfig: RunConfigSnapshot }) {
  const chips = [
    `数据源：${runConfig.sourcePreferences.join(" / ")}`,
    `模式：${PROFILE_LABELS[runConfig.runProfile]}`,
    `top_k：${formatNumber(runConfig.topK)}`,
    `查询演化：${formatBoolean(runConfig.enableQueryEvolution)}`,
    `RefChain：${formatBoolean(runConfig.enableRefchain)}`,
    `LLM 查询理解：${formatBoolean(runConfig.enableLlmQueryUnderstanding)}`,
    `LLM 判断：${formatBoolean(runConfig.enableLlmJudgement)}`,
  ];

  return (
    <div className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--surface-glass)] p-4">
      <div className="mb-3 flex items-center gap-2">
        <SlidersHorizontal className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
        <h3 className="font-semibold">本次运行配置</h3>
      </div>
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <Badge key={chip}>{chip}</Badge>
        ))}
      </div>
    </div>
  );
}

function CostMetrics({ costReport }: { costReport: CostReport | null }) {
  const metrics = [
    {
      label: "API 调用",
      value: costReport ? formatNumber(costReport.api_call_count) : "--",
      icon: Server,
    },
    {
      label: "估算 Token",
      value: costReport ? formatNumber(costReport.estimated_total_tokens) : "--",
      icon: Zap,
    },
    {
      label: "延迟",
      value: costReport ? formatSeconds(costReport.latency_seconds) : "--",
      icon: Timer,
    },
    {
      label: "缓存命中",
      value: costReport ? formatNumber(costReport.cache_hit_count) : "--",
      icon: Database,
    },
  ];

  return (
    <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map((metric) => {
        const Icon = metric.icon;
        return (
          <div key={metric.label} className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-xs font-semibold uppercase text-[var(--muted)]">{metric.label}</span>
              <Icon className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            </div>
            <p className="metric-value text-2xl font-bold">{metric.value}</p>
          </div>
        );
      })}
    </div>
  );
}

function ResultsPanel({
  result,
  isLoading,
}: {
  result: SearchRunResultResponse | null;
  isLoading: boolean;
}) {
  const visiblePaperCount = result
    ? result.highly_relevant_papers.length + result.partially_relevant_papers.length
    : 0;
  const hasDiagnosticsWithoutCandidates =
    Boolean(result) && visiblePaperCount === 0 && Boolean(result?.missing_evidence.length);

  return (
    <SectionPanel aria-labelledby="results-title" className="rounded-lg">
      <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="mb-2 text-sm font-semibold text-[var(--primary)]">检索结果</p>
          <h2 id="results-title" className="text-2xl font-black">
            论文与证据
          </h2>
          <p className="mt-1 text-sm leading-6 text-[var(--muted)]">
            优先展示可读论文卡片；运行耗时、成本、检索源错误和原始 warning 已收进诊断折叠区。
          </p>
        </div>
        {result ? (
          <div className="flex flex-col gap-3 md:items-end">
            <div className="flex flex-wrap gap-2 md:justify-end">
              <Badge>{result.highly_relevant_papers.length} 篇高度相关</Badge>
              <Badge>{result.partially_relevant_papers.length} 篇部分相关</Badge>
              <Badge>{result.search_plan.source_preferences.join(" / ")}</Badge>
            </div>
            <ResultExportActions result={result} />
          </div>
        ) : null}
      </div>

      {isLoading ? <LoadingResults /> : null}
      {!isLoading && !result ? <EmptyResults /> : null}
      {result ? (
        <div className="space-y-6">
          {hasDiagnosticsWithoutCandidates ? <SourceDiagnosticNotice result={result} /> : null}

          {result.synthesis ? <SynthesisPanel synthesis={result.synthesis} /> : null}

          <CitationGraphPanel result={result} />

          <QuerySummary result={result} />

          <PaperSection
            title="高度相关论文"
            description="直接匹配查询意图和关键约束的候选论文"
            papers={result.highly_relevant_papers}
          />

          <PaperSection
            title="部分相关论文"
            description="对方法、评测或证据组织有参考价值的论文"
            papers={result.partially_relevant_papers}
          />

          <div className="grid gap-4 lg:grid-cols-3">
            <MethodClusters result={result} />
            <Timeline result={result} />
          </div>

          <details className="details-card rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-4">
            <summary className="cursor-pointer text-sm font-bold text-[var(--foreground)]">
              技术诊断 / 调试信息
            </summary>
            <div className="mt-4 space-y-5">
              <StageLatencyPanel result={result} />
              <CostEfficiencyPanel result={result} />
              <RetrievalDiagnosticsPanel result={result} />
              <MissingEvidence result={result} />
            </div>
          </details>
        </div>
      ) : null}
    </SectionPanel>
  );
}

function ResultExportActions({ result }: { result: SearchRunResultResponse }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3">
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={() => exportSearchResultAsJson(result)}
          aria-label="导出当前结果为 JSON"
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          导出 JSON
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => exportSearchResultAsMarkdown(result)}
          aria-label="导出当前结果为 Markdown"
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
          导出 Markdown
        </Button>
      </div>
      <p className="mt-2 max-w-sm text-xs leading-5 text-[var(--muted)]">
        导出内容来自当前页面 result，不会重新检索，也不会上传到后端。
      </p>
    </div>
  );
}

function SourceDiagnosticNotice({ result }: { result: SearchRunResultResponse }) {
  const sourceCount = result.retrieval_diagnostics?.source_stats?.length ?? 0;
  return (
    <div
      role="status"
      className="rounded-lg border border-[color-mix(in_srgb,var(--warning)_60%,var(--border))] bg-[color-mix(in_srgb,var(--warning)_12%,var(--surface))] p-4"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[var(--warning)]" aria-hidden="true" />
        <div className="min-w-0">
          <h3 className="font-semibold text-[var(--foreground)]">检索源失败/无候选</h3>
          <p className="mt-1 text-sm text-[var(--muted)]">
            返回结构有效，但当前没有可展示论文。原始错误、限流、超时和检索源诊断已放入下方“技术诊断 / 调试信息”折叠区。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Badge>{formatNumber(sourceCount)} 个检索源记录</Badge>
            <Badge>{formatNumber(result.missing_evidence.length)} 条诊断</Badge>
          </div>
        </div>
      </div>
    </div>
  );
}

function StageLatencyPanel({ result }: { result: SearchRunResultResponse }) {
  const latencies = parseStageLatencies(result.missing_evidence);
  if (!latencies.length) {
    return null;
  }

  const maxSeconds = Math.max(...latencies.map((item) => item.seconds), 0.001);
  const totalSeconds = latencies.reduce((total, item) => total + item.seconds, 0);

  return (
    <section
      aria-labelledby="stage-latency-title"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5 shadow-sm"
    >
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Timer className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" />
              <h3 id="stage-latency-title" className="text-lg font-bold">
              阶段耗时
            </h3>
          </div>
          <p className="text-sm leading-6 text-[var(--muted)]">
            用于定位真实检索 pipeline 中耗时较高的阶段。
          </p>
        </div>
        <Badge>总计 {formatDetailedSeconds(totalSeconds)}</Badge>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {latencies.map((item) => {
          const width = `${Math.max(4, Math.round((item.seconds / maxSeconds) * 100))}%`;
          return (
            <div
              key={item.stage}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3"
            >
              <div className="mb-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-[var(--foreground)]">
                    {item.label}
                  </p>
                  <p className="mt-1 font-mono text-xs text-[var(--muted)]">
                    {item.stage}
                  </p>
                </div>
                <span className="font-mono text-sm font-semibold text-[var(--primary)]">
                  {formatDetailedSeconds(item.seconds)}
                </span>
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-[var(--surface-soft)]"
                aria-hidden="true"
              >
                <div
                  className="h-full rounded-full bg-[var(--primary)]"
                  style={{ width }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function CostEfficiencyPanel({ result }: { result: SearchRunResultResponse }) {
  const costReport = result.cost_report;
  const metrics = [
    {
      label: "总 API 调用",
      value: formatNumber(costValue(costReport, "api_call_count")),
      icon: Server,
    },
    {
      label: "检索 API 调用",
      value: formatNumber(costValue(costReport, "search_api_call_count")),
      icon: Search,
    },
    {
      label: "缓存命中",
      value: formatNumber(costValue(costReport, "cache_hit_count")),
      icon: Database,
    },
    {
      label: "LLM 调用",
      value: formatNumber(costValue(costReport, "llm_call_count")),
      icon: Brain,
    },
    {
      label: "LLM 输入 Token",
      value: formatNumber(costValue(costReport, "llm_prompt_tokens")),
      icon: Zap,
    },
    {
      label: "LLM 输出 Token",
      value: formatNumber(costValue(costReport, "llm_completion_tokens")),
      icon: Zap,
    },
    {
      label: "LLM 总 Token",
      value: formatNumber(costValue(costReport, "llm_total_tokens")),
      icon: Zap,
    },
    {
      label: "估算输入 Token",
      value: formatNumber(costValue(costReport, "estimated_input_tokens")),
      icon: Timer,
    },
    {
      label: "估算输出 Token",
      value: formatNumber(costValue(costReport, "estimated_output_tokens")),
      icon: Timer,
    },
    {
      label: "估算总 Token",
      value: formatNumber(costValue(costReport, "estimated_total_tokens")),
      icon: Timer,
    },
  ];

  return (
    <section
      aria-labelledby="cost-efficiency-title"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5 shadow-sm"
    >
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Zap className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" />
            <h3 id="cost-efficiency-title" className="text-lg font-bold">
              成本与效率
            </h3>
          </div>
          <p className="text-sm leading-6 text-[var(--muted)]">
            展示 API 调用、缓存命中与 LLM token 统计；不包含任何 API key。
          </p>
        </div>
        <Badge>延迟 {formatDetailedSeconds(costValue(costReport, "latency_seconds"))}</Badge>
      </div>

      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <div
              key={metric.label}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3"
            >
              <dt className="flex min-h-10 items-start justify-between gap-2 text-xs font-semibold uppercase text-[var(--muted)]">
                <span className="break-words">{metric.label}</span>
                <Icon className="h-4 w-4 shrink-0 text-[var(--primary)]" aria-hidden="true" />
              </dt>
              <dd className="metric-value mt-2 text-lg font-bold text-[var(--foreground)]">
                {metric.value}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

function RetrievalDiagnosticsPanel({ result }: { result: SearchRunResultResponse }) {
  const diagnostics = result.retrieval_diagnostics;
  const sourceStats = diagnostics?.source_stats ?? [];
  const hasCounts =
    typeof diagnostics?.raw_count === "number" ||
    typeof diagnostics?.deduplicated_count === "number";

  if (!hasCounts && sourceStats.length === 0) {
    return null;
  }

  return (
    <section
      aria-labelledby="retrieval-diagnostics-title"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5 shadow-sm"
    >
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Database className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" />
            <h3 id="retrieval-diagnostics-title" className="text-lg font-bold">
              检索诊断
            </h3>
          </div>
          <p className="text-sm leading-6 text-[var(--muted)]">
            候选规模与检索源状态来自后端输出，用于观察跨源召回、去重和缓存命中情况。
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:min-w-64">
          <DiagnosticMetric label="原始候选" value={formatNumber(diagnostics?.raw_count ?? 0)} />
          <DiagnosticMetric
            label="去重后"
            value={formatNumber(diagnostics?.deduplicated_count ?? 0)}
          />
        </div>
      </div>

      {sourceStats.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {sourceStats.map((stat, index) => (
            <div
              key={`${stat.source}-${index}`}
              className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-semibold text-[var(--foreground)]">{stat.source}</p>
                  <p className="mt-1 font-mono text-xs text-[var(--muted)]">
                    {formatDetailedSeconds(stat.latency_seconds)}
                  </p>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <Badge>{formatNumber(stat.returned_count)} 条返回</Badge>
                  <Badge>{stat.cache_hit ? "缓存命中" : "未命中缓存"}</Badge>
                </div>
              </div>
              {stat.error_message ? (
                <div className="mt-3 rounded-md border border-[color-mix(in_srgb,var(--warning)_55%,var(--border))] bg-[color-mix(in_srgb,var(--warning)_10%,var(--surface))] px-3 py-2 text-sm leading-5 text-[var(--muted-strong)]">
                  {stat.error_message}
                </div>
              ) : (
                <p className="mt-3 text-sm text-[var(--muted)]">无连接器错误。</p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--muted)]">
          后端未返回检索源统计。
        </p>
      )}
    </section>
  );
}

function DiagnosticMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <span className="block text-xs font-semibold uppercase text-[var(--muted)]">
        {label}
      </span>
      <span className="mt-1 block font-mono text-lg font-semibold text-[var(--foreground)]">
        {value}
      </span>
    </div>
  );
}

function SynthesisPanel({ synthesis }: { synthesis: SynthesisOutput }) {
  const coverage = synthesis.citation_coverage;
  const evidenceRows = synthesis.evidence_table.slice(0, 6);
  const limitationItems = [...synthesis.limitations, ...synthesis.warnings];
  const coverageMetrics = [
    {
      label: "排序论文",
      value: formatNumber(coverage.ranked_paper_count),
    },
    {
      label: "引用论文",
      value: formatNumber(coverage.cited_paper_count),
    },
    {
      label: "证据行",
      value: formatNumber(coverage.evidence_row_count),
    },
    {
      label: "覆盖率",
      value: formatScore(coverage.coverage_ratio),
    },
    {
      label: "源错误",
      value: formatNumber(coverage.source_error_count),
    },
  ];

  return (
    <section
      aria-labelledby="synthesis-title"
      className="rounded-lg border border-[color-mix(in_srgb,var(--accent)_45%,var(--border))] bg-[color-mix(in_srgb,var(--accent)_7%,var(--surface-raised))] p-5 shadow-sm"
    >
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Sparkles className="h-5 w-5 text-[var(--accent)]" aria-hidden="true" />
            <h3 id="synthesis-title" className="text-lg font-bold">
              引文支撑归纳
            </h3>
            <Badge>{synthesis.status}</Badge>
          </div>
          <p className="text-sm leading-6 text-[var(--muted-strong)]">
            {synthesis.answer_summary}
          </p>
          <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
            规则版元数据与证据行归纳；当前 MVP 不代表系统已读取全文 PDF。
          </p>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {coverageMetrics.map((metric) => (
          <div
            key={metric.label}
            className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3"
          >
            <p className="text-xs font-semibold uppercase text-[var(--muted)]">
              {metric.label}
            </p>
            <p className="metric-value mt-1 text-lg font-bold text-[var(--foreground)]">
              {metric.value}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-[1fr_0.9fr]">
        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="mb-3 flex items-center gap-2">
            <BookOpenCheck className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h4 className="font-semibold">关键发现</h4>
          </div>
          {synthesis.key_findings.length ? (
            <div className="space-y-3">
              {synthesis.key_findings.map((finding, index) => (
                <div
                  key={`${finding.text}-${index}`}
                  className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3"
                >
                  <p className="text-sm leading-6 text-[var(--muted-strong)]">{finding.text}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {finding.citation_keys.map((key) => (
                      <Badge key={key}>{key}</Badge>
                    ))}
                    <Badge>{formatScore(finding.confidence)}</Badge>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-[var(--muted)]">暂无可引用 finding。</p>
          )}
        </div>

        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="mb-3 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-[var(--warning)]" aria-hidden="true" />
            <h4 className="font-semibold">限制与提示</h4>
          </div>
          {limitationItems.length ? (
            <div className="space-y-2">
              {limitationItems.slice(0, 8).map((item) => (
                <div
                  key={item}
                  className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-3 py-2 text-sm text-[var(--muted-strong)]"
                >
                  {item}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-[var(--muted)]">当前归纳未返回额外限制。</p>
          )}
        </div>
      </div>

      <div className="mt-5 rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
        <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h4 className="font-semibold">证据表</h4>
            <p className="text-sm text-[var(--muted)]">展示前 {evidenceRows.length} 条证据行。</p>
          </div>
          <Badge>{formatNumber(synthesis.evidence_table.length)} 行</Badge>
        </div>
        {evidenceRows.length ? (
          <div className="grid gap-3">
            {evidenceRows.map((row) => (
              <div
                key={row.row_id}
                className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge>{row.citation_key}</Badge>
                  <Badge>第 {row.rank} 名</Badge>
                  {row.year ? <Badge>{row.year}</Badge> : null}
                  <Badge>{row.evidence_source}</Badge>
                </div>
                <p className="font-semibold leading-snug text-[var(--foreground)]">
                  {row.paper_title}
                </p>
                <p className="mt-2 text-sm leading-6 text-[var(--muted-strong)]">
                  {row.evidence_text}
                </p>
              </div>
            ))}
          </div>
        ) : (
        <p className="text-sm text-[var(--muted)]">暂无证据行。</p>
        )}
      </div>
    </section>
  );
}

function CitationGraphPanel({ result }: { result: SearchRunResultResponse }) {
  const nodes = result.citation_graph?.nodes ?? [];
  const edges = result.citation_graph?.edges ?? [];

  if (!nodes.length && !edges.length) {
    return null;
  }

  return (
    <section
      aria-labelledby="citation-graph-title"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5 shadow-sm"
    >
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Network className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" />
            <h3 id="citation-graph-title" className="text-lg font-bold">
              引用图谱
            </h3>
            <Badge>{formatNumber(nodes.length)} 个节点</Badge>
            <Badge>{formatNumber(edges.length)} 条边</Badge>
          </div>
          <p className="max-w-4xl text-sm leading-6 text-[var(--muted)]">
            当前图谱只展示后端返回的 citation_graph / RefChain metadata；前端不推断未返回的引用关系。
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="font-semibold">节点</h4>
            <Badge>{formatNumber(nodes.length)}</Badge>
          </div>
          <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {nodes.map((node) => (
              <div
                key={node.id}
                className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  {node.rank ? <Badge>第 {node.rank} 名</Badge> : <Badge>未排序</Badge>}
                  <Badge>节点</Badge>
                </div>
                <p className="break-words text-sm font-semibold leading-5 text-[var(--foreground)]">
                  {node.label}
                </p>
                <p className="mt-2 break-all font-mono text-xs leading-5 text-[var(--muted)]">
                  {node.id}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="font-semibold">关系边</h4>
            <Badge>{formatNumber(edges.length)}</Badge>
          </div>
          {edges.length ? (
            <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
              {edges.map((edge, index) => (
                <div
                  key={`${edge.source}-${edge.target}-${edge.relation}-${index}`}
                  className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3"
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Badge>{edge.relation}</Badge>
                    <Badge>第 {index + 1} 条边</Badge>
                  </div>
                  <div className="grid gap-2 text-xs">
                    <GraphEndpoint label="来源节点" value={edge.source} />
                    <GraphEndpoint label="目标节点" value={edge.target} />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-md border border-dashed border-[var(--border)] bg-[var(--surface-raised)] p-4">
              <p className="text-sm font-semibold text-[var(--foreground)]">
                当前无引用边/关系边
              </p>
              <p className="mt-1 text-sm leading-6 text-[var(--muted)]">
                后端返回了图谱节点，但未返回引用关系边。真实检索在未启用
                RefChain 或没有可用引用元数据时可能出现这种状态。
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function parseStageLatencies(missingEvidence: string[]): StageLatencyItem[] {
  const byStage = new Map<string, number>();
  missingEvidence.forEach((item) => {
    const match = /^stage_latency:([^:]+):(.+)$/.exec(item.trim());
    if (!match) {
      return;
    }
    const stage = match[1];
    const seconds = Number(match[2]);
    if (!stage || !Number.isFinite(seconds) || seconds < 0) {
      return;
    }
    byStage.set(stage, seconds);
  });

  return Array.from(byStage.entries())
    .map(([stage, seconds]) => ({
      stage,
      label: STAGE_LATENCY_LABELS[stage] ?? stage,
      seconds,
    }))
    .sort((left, right) => {
      const leftIndex = STAGE_LATENCY_ORDER.indexOf(left.stage);
      const rightIndex = STAGE_LATENCY_ORDER.indexOf(right.stage);
      const normalizedLeft = leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex;
      const normalizedRight = rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex;
      if (normalizedLeft !== normalizedRight) {
        return normalizedLeft - normalizedRight;
      }
      return left.stage.localeCompare(right.stage);
    });
}

function formatDetailedSeconds(seconds: number): string {
  if (seconds < 1) {
    return `${seconds.toFixed(3)}s`;
  }
  if (seconds < 10) {
    return `${seconds.toFixed(2)}s`;
  }
  return `${seconds.toFixed(1)}s`;
}

function GraphEndpoint({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2">
      <span className="block text-xs font-semibold uppercase text-[var(--muted)]">
        {label}
      </span>
      <span className="mt-1 block break-all font-mono text-xs leading-5 text-[var(--foreground)]">
        {value}
      </span>
    </div>
  );
}

function QuerySummary({ result }: { result: SearchRunResultResponse }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
      <div className="panel-soft rounded-lg p-4">
        <h3 className="mb-3 font-semibold">查询理解</h3>
        <div className="flex flex-wrap gap-2">
          <Badge>{result.query_analysis.intent_type}</Badge>
          <Badge>{result.query_analysis.domain}</Badge>
          {result.query_analysis.research_topics.map((topic) => (
            <Badge key={topic}>{topic}</Badge>
          ))}
        </div>
      </div>
      <div className="panel-soft rounded-lg p-4">
        <h3 className="mb-3 font-semibold">扩展检索式</h3>
        <div className="space-y-2">
          {result.search_plan.expanded_queries.map((expandedQuery, index) => (
            <div key={`${expandedQuery}-${index}`} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
              <span className="mr-2 font-semibold text-[var(--primary)]">{index + 1}</span>
              {expandedQuery}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function PaperSection({
  title,
  description,
  papers,
}: {
  title: string;
  description: string;
  papers: RankedPaper[];
}) {
  return (
    <section aria-label={title}>
      <div className="mb-3">
        <h3 className="text-lg font-bold">{title}</h3>
        <p className="text-sm text-[var(--muted)]">{description}</p>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {papers.map((paper) => (
          <PaperCard key={`${paper.rank}-${paper.paper.title}`} paper={paper} />
        ))}
      </div>
    </section>
  );
}

function PaperCard({ paper }: { paper: RankedPaper }) {
  const identifiers = identifierEntries(paper.paper.identifiers);

  return (
    <article className="card paper-card result-paper-card">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge>第 {paper.rank} 名</Badge>
            <Badge>{paper.paper.year || "年份未知"}</Badge>
            {paper.paper.venue ? <Badge>{paper.paper.venue}</Badge> : null}
            <Badge>相关性 {formatScore(paper.relevance_score)}</Badge>
            <Badge>{categoryLabel(paper.category)}</Badge>
          </div>
          <h4 className="card__title">{paper.paper.title}</h4>
          <p className="card__content mt-2">
            {paper.paper.authors.length ? paper.paper.authors.join(", ") : "作者信息暂缺"}
          </p>
        </div>
      </div>

      <details className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
        <summary className="cursor-pointer text-sm font-bold text-[var(--foreground)]">
          摘要
        </summary>
        <p className="mt-3 text-sm leading-6 text-[var(--muted-strong)]">
          {paper.paper.abstract || "当前结果未返回摘要。"}
        </p>
      </details>

      <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3">
        <p className="text-sm font-semibold">相关性说明</p>
        <p className="mt-1 text-sm text-[var(--muted)]">{paper.ranking_reason}</p>
      </div>

      {paper.evidence.length ? (
        <div className="mt-4 space-y-2">
          <p className="text-sm font-semibold">证据</p>
          {paper.evidence.map((item) => (
            <div key={`${item.source}-${item.text}`} className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge>{item.source}</Badge>
                <Badge>置信度 {formatScore(item.confidence)}</Badge>
              </div>
              <p className="text-[var(--muted)]">{item.text}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {paper.paper.sources.map((source) => (
          <Badge key={source}>来源：{source}</Badge>
        ))}
      </div>

      {identifiers.length ? (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {identifiers.map(([label, value]) => (
            <div key={`${label}-${value}`} className="rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs">
              <span className="block font-semibold text-[var(--muted)]">{label}</span>
              <span className="mt-1 block break-words text-[var(--foreground)]">{value}</span>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {paper.paper.urls.landing_page ? (
          <a
            href={paper.paper.urls.landing_page}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-[var(--border)] px-4 text-sm font-semibold text-[var(--primary)] transition duration-200 hover:border-[var(--primary)]"
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            打开论文页
          </a>
        ) : null}
        {paper.paper.urls.pdf ? (
          <a
            href={paper.paper.urls.pdf}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-[var(--border)] px-4 text-sm font-semibold text-[var(--primary)] transition duration-200 hover:border-[var(--primary)]"
          >
            <FileText className="h-4 w-4" aria-hidden="true" />
            PDF
          </a>
        ) : null}
      </div>
    </article>
  );
}

function MethodClusters({ result }: { result: SearchRunResultResponse }) {
  return (
    <div className="panel-soft rounded-lg p-4">
      <h3 className="mb-3 font-semibold">方法聚类</h3>
      <div className="space-y-3">
        {result.method_clusters.map((cluster) => (
          <div key={cluster.name} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3">
            <p className="font-semibold">{cluster.name}</p>
            <p className="mt-1 text-sm text-[var(--muted)]">{cluster.summary}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {cluster.paper_ranks.map((rank) => (
                <Badge key={rank}>第 {rank} 名</Badge>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Timeline({ result }: { result: SearchRunResultResponse }) {
  return (
    <div className="panel-soft rounded-lg p-4">
      <h3 className="mb-3 font-semibold">时间线</h3>
      <div className="space-y-3">
        {result.timeline.map((item) => (
          <div key={item.year} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3">
            <p className="metric-value text-lg font-bold text-[var(--primary)]">{item.year}</p>
            <p className="mt-1 text-sm text-[var(--muted)]">{item.summary}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {item.paper_ranks.map((rank) => (
                <Badge key={rank}>第 {rank} 名</Badge>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MissingEvidence({ result }: { result: SearchRunResultResponse }) {
  return (
    <div className="panel-soft rounded-lg p-4">
      <h3 className="mb-3 font-semibold">原始提示与证据缺口</h3>
      <p className="mb-3 text-sm leading-6 text-[var(--muted)]">
        这里集中展示 503、429、timeout、cooldown、source_error 等后端原始诊断，默认折叠以避免干扰主要阅读。
      </p>
      <div className="space-y-2">
        {result.missing_evidence.map((item) => (
          <div key={item} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3 text-sm text-[var(--muted)]">
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase text-[var(--muted)]">{label}</dt>
      <dd className="metric-value mt-1 text-base font-bold text-[var(--foreground)]">{value}</dd>
    </div>
  );
}

function formatBoolean(value: boolean): string {
  return value ? "开启" : "关闭";
}

function statusLabel(status: SearchRunStatusResponse["status"]): string {
  const labels: Record<SearchRunStatusResponse["status"], string> = {
    queued: "排队中",
    running: "运行中",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

function eventNameLabel(eventName: string): string {
  const labels: Record<string, string> = {
    run_started: "任务开始",
    stage_started: "阶段开始",
    stage_completed: "阶段完成",
    connector_completed: "检索源完成",
    warning: "提示",
    cost_updated: "成本更新",
    error: "错误",
    run_completed: "任务结束",
    sse_error: "事件连接异常",
  };
  return labels[eventName] ?? eventName;
}

function categoryLabel(category: RankedPaper["category"]): string {
  const labels: Record<RankedPaper["category"], string> = {
    highly_relevant: "高度相关",
    partially_relevant: "部分相关",
    weakly_relevant: "弱相关",
    irrelevant: "不相关",
    insufficient_evidence: "证据不足",
  };
  return labels[category] ?? category;
}

function costValue(
  costReport: CostReport | null | undefined,
  key: keyof CostReport,
): number {
  const value = costReport?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function EmptyBlock({ lines }: { lines: number }) {
  return (
    <div className="space-y-3" aria-hidden="true">
      {Array.from({ length: lines }).map((_, index) => (
        <SkeletonLine key={index} className={index === lines - 1 ? "w-2/3" : "w-full"} />
      ))}
    </div>
  );
}

function EmptyResults() {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border)] bg-[var(--surface-raised)] p-8 text-center">
      <FileText className="mx-auto mb-3 h-8 w-8 text-[var(--primary)]" aria-hidden="true" />
      <h3 className="text-lg font-bold">暂无检索结果</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm text-[var(--muted)]">
        创建真实检索任务后，这里会展示高度相关论文、部分相关论文、方法聚类、时间线和证据缺口。
      </p>
    </div>
  );
}

function LoadingResults() {
  return (
    <div className="grid gap-4 md:grid-cols-2" aria-label="结果加载中">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5">
          <SkeletonLine className="mb-4 w-1/3" />
          <SkeletonLine className="mb-3 w-full" />
          <SkeletonLine className="mb-3 w-5/6" />
          <SkeletonLine className="w-2/3" />
        </div>
      ))}
    </div>
  );
}
