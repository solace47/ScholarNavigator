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
  RefreshCw,
  Search,
  Server,
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
  "Find benchmark papers for scientific literature search agents that evaluate recall, precision, F1, and end-to-end latency.",
  "搜索使用 citation graph 或 reference chain 扩展来提升论文推荐召回率的研究，并说明代表性方法路线。",
];

const STAGES = [
  {
    key: "query_understanding",
    title: "Query Understanding",
    icon: Brain,
  },
  {
    key: "retrieval",
    title: "Retrieval",
    icon: Database,
  },
  {
    key: "judgement",
    title: "Judgement",
    icon: BookOpenCheck,
  },
  {
    key: "reranking",
    title: "Reranking",
    icon: GitBranch,
  },
  {
    key: "synthesis",
    title: "Synthesis",
    icon: Sparkles,
  },
];

const PROFILE_LABELS: Record<RunProfile, string> = {
  fast: "fast",
  balanced: "balanced",
  high_recall: "high_recall",
  evaluation: "evaluation",
};

type ThemeMode = "dark" | "light";

export function ScholarNavigatorApp() {
  const [theme, setTheme] = useState<ThemeMode>("dark");
  const [query, setQuery] = useState(EXAMPLES[0]);
  const [topK, setTopK] = useState(20);
  const [currentYear, setCurrentYear] = useState(2026);
  const [runProfile, setRunProfile] = useState<RunProfile>("balanced");
  const [enableRefchain, setEnableRefchain] = useState(true);
  const [enableQueryEvolution, setEnableQueryEvolution] = useState(true);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigResponse | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<SearchRunStatusResponse | null>(null);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [result, setResult] = useState<SearchRunResultResponse | null>(null);
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
          setBackendError("后端服务不可用，请先启动 FastAPI Real Search API");
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
        source_preferences: ["openalex", "arxiv"],
        run_profile: runProfile,
        top_k: topK,
        budgets: buildBudgets(runProfile),
        options: {
          enable_query_evolution: enableQueryEvolution,
          enable_refchain: enableRefchain,
          refchain_depth: enableRefchain ? 1 : 0,
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
            : "后端服务不可用，请先启动 FastAPI Real Search API",
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
          let message = "Real Search failed";
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
          : "取消 Real Search 失败，请稍后重试。",
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
          runtimeConfig={runtimeConfig}
          backendError={backendError}
        />

        {backendError ? <BackendWarning message={backendError} /> : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(380px,0.9fr)_minmax(0,1.4fr)]">
          <SearchWorkbench
            query={query}
            topK={topK}
            currentYear={currentYear}
            runProfile={runProfile}
            enableRefchain={enableRefchain}
            enableQueryEvolution={enableQueryEvolution}
            isSubmitting={isSubmitting}
            formError={formError}
            onQueryChange={setQuery}
            onTopKChange={setTopK}
            onCurrentYearChange={setCurrentYear}
            onRunProfileChange={setRunProfile}
            onRefchainChange={setEnableRefchain}
            onQueryEvolutionChange={setEnableQueryEvolution}
            onSearch={handleSearch}
          />

          <RunProgress
            runId={runId}
            status={status}
            events={events}
            costReport={costReport}
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function runtimeModeLabel(runtimeConfig: RuntimeConfigResponse | null): string {
  if (!runtimeConfig) {
    return "runtime loading";
  }
  if (runtimeConfig.features.real_search) {
    return "Real Search Runtime";
  }
  return runtimeConfig.mode;
}

function Header({
  theme,
  onThemeChange,
  runtimeConfig,
  backendError,
}: {
  theme: ThemeMode;
  onThemeChange: () => void;
  runtimeConfig: RuntimeConfigResponse | null;
  backendError: string | null;
}) {
  return (
    <header className="panel flex flex-col gap-4 rounded-lg px-5 py-5 md:px-6 lg:flex-row lg:items-center lg:justify-between">
      <div className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <span className="inline-flex min-h-10 items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--surface-soft)] px-3 text-sm font-semibold text-[var(--muted-strong)]">
            <Network className="h-4 w-4 text-[var(--accent)]" aria-hidden="true" />
            Agent Workbench
          </span>
          <Badge>{runtimeModeLabel(runtimeConfig)}</Badge>
          {runtimeConfig?.features.real_search ? <Badge>Real Search</Badge> : null}
          {runtimeConfig?.features.llm_query_understanding ? (
            <Badge>LLM Query Understanding</Badge>
          ) : null}
          {runtimeConfig?.llm.available === false ? <Badge>rules QA / no-LLM</Badge> : null}
          <Badge className={backendError ? "text-[var(--danger)]" : "text-[var(--accent)]"}>
            {backendError ? "backend offline" : "backend ready"}
          </Badge>
        </div>
        <h1 className="text-3xl font-bold leading-tight md:text-5xl">ScholarNavigator</h1>
        <p className="mt-2 max-w-3xl text-base text-[var(--muted)] md:text-lg">
          复杂学术查询的智能论文搜索与推荐系统
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
        {theme === "dark" ? "Light" : "Dark"}
      </Button>
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
  enableRefchain,
  enableQueryEvolution,
  isSubmitting,
  formError,
  onQueryChange,
  onTopKChange,
  onCurrentYearChange,
  onRunProfileChange,
  onRefchainChange,
  onQueryEvolutionChange,
  onSearch,
}: {
  query: string;
  topK: number;
  currentYear: number;
  runProfile: RunProfile;
  enableRefchain: boolean;
  enableQueryEvolution: boolean;
  isSubmitting: boolean;
  formError: string | null;
  onQueryChange: (value: string) => void;
  onTopKChange: (value: number) => void;
  onCurrentYearChange: (value: number) => void;
  onRunProfileChange: (value: RunProfile) => void;
  onRefchainChange: (value: boolean) => void;
  onQueryEvolutionChange: (value: boolean) => void;
  onSearch: () => void;
}) {
  return (
    <SectionPanel aria-labelledby="search-workbench-title" className="h-fit">
      <div className="mb-5 flex items-center justify-between gap-4">
        <div>
          <h2 id="search-workbench-title" className="text-xl font-bold">
            Search Workbench
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">真实检索、预算与 Agent 策略配置</p>
        </div>
        <Search className="h-5 w-5 text-[var(--primary)]" aria-hidden="true" />
      </div>

      <div className="space-y-5">
        <div>
          <FieldLabel htmlFor="query">学术查询</FieldLabel>
          <textarea
            id="query"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            className="control min-h-44 w-full resize-y rounded-md px-4 py-3 text-base"
            placeholder="输入中文或英文复杂学术查询"
          />
          {formError ? <p className="mt-2 text-sm text-[var(--danger)]">{formError}</p> : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <FieldLabel htmlFor="top-k">top_k</FieldLabel>
            <TextInput
              id="top-k"
              type="number"
              min={1}
              max={100}
              value={topK}
              onChange={(event) => onTopKChange(Number(event.target.value))}
            />
          </div>
          <div>
            <FieldLabel htmlFor="current-year">current_year</FieldLabel>
            <TextInput
              id="current-year"
              type="number"
              min={1900}
              max={2100}
              value={currentYear}
              onChange={(event) => onCurrentYearChange(Number(event.target.value))}
            />
          </div>
          <div>
            <FieldLabel htmlFor="run-profile">run_profile</FieldLabel>
            <select
              id="run-profile"
              value={runProfile}
              onChange={(event) => onRunProfileChange(event.target.value as RunProfile)}
              className="control w-full rounded-md px-3 py-2 text-sm"
            >
              {(Object.keys(PROFILE_LABELS) as RunProfile[]).map((profile) => (
                <option key={profile} value={profile}>
                  {PROFILE_LABELS[profile]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <ToggleControl
            label="enable_refchain"
            description="单层引用扩展"
            checked={enableRefchain}
            onChange={onRefchainChange}
          />
          <ToggleControl
            label="enable_query_evolution"
            description="查询演化"
            checked={enableQueryEvolution}
            onChange={onQueryEvolutionChange}
          />
        </div>

        <div>
          <p className="mb-2 text-sm font-semibold text-[var(--muted-strong)]">示例查询</p>
          <div className="grid gap-2">
            {EXAMPLES.map((example, index) => (
              <button
                key={example}
                type="button"
                onClick={() => onQueryChange(example)}
                className="min-h-11 rounded-md border border-[var(--border)] bg-[var(--surface-raised)] px-3 py-2 text-left text-sm text-[var(--muted-strong)] transition duration-200 hover:border-[var(--primary)] hover:text-[var(--foreground)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
              >
                <span className="mr-2 font-semibold text-[var(--primary)]">0{index + 1}</span>
                {example}
              </button>
            ))}
          </div>
        </div>

        <Button type="button" variant="primary" className="w-full" onClick={onSearch} disabled={isSubmitting}>
          {isSubmitting ? (
            <RefreshCw className="h-4 w-4 motion-safe:animate-spin" aria-hidden="true" />
          ) : (
            <Search className="h-4 w-4" aria-hidden="true" />
          )}
          {isSubmitting ? "Real Search running" : "启动 Real Search"}
        </Button>
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
    <button
      type="button"
      aria-pressed={checked}
      onClick={() => onChange(!checked)}
      className="flex min-h-20 items-center justify-between gap-3 rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3 text-left transition duration-200 hover:border-[var(--primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--primary)]"
    >
      <span>
        <span className="block text-sm font-semibold text-[var(--foreground)]">{label}</span>
        <span className="mt-1 block text-xs text-[var(--muted)]">{description}</span>
      </span>
      <span
        className={`relative h-6 w-11 shrink-0 rounded-full border transition duration-200 ${
          checked
            ? "border-[var(--accent)] bg-[var(--accent)]"
            : "border-[var(--border-strong)] bg-[var(--surface-soft)]"
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition duration-200 ${
            checked ? "left-5" : "left-0.5"
          }`}
        />
      </span>
    </button>
  );
}

function RunProgress({
  runId,
  status,
  events,
  costReport,
  isSubmitting,
  isCancelling,
  onCancelRealSearch,
}: {
  runId: string | null;
  status: SearchRunStatusResponse | null;
  events: StreamEvent[];
  costReport: CostReport | null;
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
    <SectionPanel aria-labelledby="run-progress-title">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 id="run-progress-title" className="text-xl font-bold">
            Run Progress
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">
            {runId ? `run_id: ${runId}` : "等待创建检索任务"}
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
              取消 Real Search
            </Button>
          ) : null}
          <Badge className={isSubmitting ? "text-[var(--warning)]" : "text-[var(--accent)]"}>
            {isSubmitting ? status?.status ?? "running" : status?.status ?? "idle"}
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
              <p className="mt-1 text-xs text-[var(--muted)]">{stage.key}</p>
            </div>
          );
        })}
      </div>

      <CostMetrics costReport={costReport} />

      <div className="mt-5 grid gap-4 lg:grid-cols-[1fr_1.1fr]">
        <div className="panel-soft rounded-lg p-4">
          <div className="mb-3 flex items-center gap-2">
            <Activity className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h3 className="font-semibold">状态摘要</h3>
          </div>
          {status ? (
            <dl className="grid gap-3 text-sm sm:grid-cols-2">
              <MetricRow label="current_stage" value={status.current_stage} />
              <MetricRow label="candidate_paper_count" value={status.progress.candidate_paper_count} />
              <MetricRow label="judged_paper_count" value={status.progress.judged_paper_count} />
              <MetricRow label="completed_stages" value={status.progress.completed_stages.length} />
            </dl>
          ) : (
            <EmptyBlock lines={3} />
          )}
        </div>

        <div className="panel-soft rounded-lg p-4">
          <div className="mb-3 flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <h3 className="font-semibold">Real Search Events</h3>
          </div>
          {events.length ? (
            <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
              {events.map((event, index) => (
                <div
                  key={`${event.event}-${index}`}
                  className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge>{event.event}</Badge>
                    {typeof event.payload.stage === "string" ? <Badge>{event.payload.stage}</Badge> : null}
                    {typeof event.payload.connector === "string" ? (
                      <Badge>{event.payload.connector}</Badge>
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
    </SectionPanel>
  );
}

function CostMetrics({ costReport }: { costReport: CostReport | null }) {
  const metrics = [
    {
      label: "API calls",
      value: costReport ? formatNumber(costReport.api_call_count) : "--",
      icon: Server,
    },
    {
      label: "Tokens",
      value: costReport ? formatNumber(costReport.estimated_total_tokens) : "--",
      icon: Zap,
    },
    {
      label: "Latency",
      value: costReport ? formatSeconds(costReport.latency_seconds) : "--",
      icon: Timer,
    },
    {
      label: "Cache hits",
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
    <SectionPanel aria-labelledby="results-title">
      <div className="mb-6 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 id="results-title" className="text-xl font-bold">
            Results
          </h2>
          <p className="mt-1 text-sm text-[var(--muted)]">结构化论文列表、方法聚类、时间线与证据缺口</p>
        </div>
        {result ? (
          <div className="flex flex-col gap-3 md:items-end">
            <div className="flex flex-wrap gap-2 md:justify-end">
              <Badge>{result.highly_relevant_papers.length} highly relevant</Badge>
              <Badge>{result.partially_relevant_papers.length} partially relevant</Badge>
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
            <MissingEvidence result={result} />
          </div>
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
          aria-label="Export current result as JSON"
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          Export JSON
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => exportSearchResultAsMarkdown(result)}
          aria-label="Export current result as Markdown"
        >
          <FileText className="h-4 w-4" aria-hidden="true" />
          Export Markdown
        </Button>
      </div>
      <p className="mt-2 max-w-sm text-xs leading-5 text-[var(--muted)]">
        导出内容来自当前页面 result，不会重新检索，也不会上传到后端。
      </p>
    </div>
  );
}

function SourceDiagnosticNotice({ result }: { result: SearchRunResultResponse }) {
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
            返回结构有效，但当前没有可展示论文。以下诊断来自 missing_evidence。
          </p>
          <div className="mt-3 grid gap-2">
            {result.missing_evidence.slice(0, 6).map((item) => (
              <div
                key={item}
                className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--muted-strong)]"
              >
                {item}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function SynthesisPanel({ synthesis }: { synthesis: SynthesisOutput }) {
  const coverage = synthesis.citation_coverage;
  const evidenceRows = synthesis.evidence_table.slice(0, 6);
  const limitationItems = [...synthesis.limitations, ...synthesis.warnings];
  const coverageMetrics = [
    {
      label: "Ranked",
      value: formatNumber(coverage.ranked_paper_count),
    },
    {
      label: "Cited",
      value: formatNumber(coverage.cited_paper_count),
    },
    {
      label: "Evidence",
      value: formatNumber(coverage.evidence_row_count),
    },
    {
      label: "Coverage",
      value: formatScore(coverage.coverage_ratio),
    },
    {
      label: "Source errors",
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
              Citation-backed Synthesis
            </h3>
            <Badge>{synthesis.status}</Badge>
          </div>
          <p className="text-sm leading-6 text-[var(--muted-strong)]">
            {synthesis.answer_summary}
          </p>
          <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
            规则版 metadata/evidence-row synthesis；当前 MVP 不代表系统已读取全文 PDF。
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
            <h4 className="font-semibold">Key Findings</h4>
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
            <h4 className="font-semibold">Limitations / Warnings</h4>
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
            <p className="text-sm text-[var(--muted)]">当前 synthesis 未返回额外限制。</p>
          )}
        </div>
      </div>

      <div className="mt-5 rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
        <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h4 className="font-semibold">Evidence Table</h4>
            <p className="text-sm text-[var(--muted)]">展示前 {evidenceRows.length} 条证据行。</p>
          </div>
          <Badge>{formatNumber(synthesis.evidence_table.length)} rows</Badge>
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
                  <Badge>rank {row.rank}</Badge>
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
          <p className="text-sm text-[var(--muted)]">暂无 evidence row。</p>
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
              Citation Graph
            </h3>
            <Badge>{formatNumber(nodes.length)} nodes</Badge>
            <Badge>{formatNumber(edges.length)} edges</Badge>
          </div>
          <p className="max-w-4xl text-sm leading-6 text-[var(--muted)]">
            当前图谱只展示后端返回的 citation_graph / RefChain metadata；前端不推断未返回的引用关系。
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h4 className="font-semibold">Nodes</h4>
            <Badge>{formatNumber(nodes.length)}</Badge>
          </div>
          <div className="max-h-80 space-y-2 overflow-y-auto pr-1">
            {nodes.map((node) => (
              <div
                key={node.id}
                className="rounded-md border border-[var(--border)] bg-[var(--surface-raised)] p-3"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  {node.rank ? <Badge>rank {node.rank}</Badge> : <Badge>unranked</Badge>}
                  <Badge>node</Badge>
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
            <h4 className="font-semibold">Edges</h4>
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
                    <Badge>edge {index + 1}</Badge>
                  </div>
                  <div className="grid gap-2 text-xs">
                    <GraphEndpoint label="source" value={edge.source} />
                    <GraphEndpoint label="target" value={edge.target} />
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
                后端返回了 graph nodes，但未返回 citation_graph.edges。Real Search 在未启用
                RefChain 或没有可用引用元数据时可能出现这种状态。
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
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
        <h3 className="mb-3 font-semibold">Query Analysis</h3>
        <div className="flex flex-wrap gap-2">
          <Badge>{result.query_analysis.intent_type}</Badge>
          <Badge>{result.query_analysis.domain}</Badge>
          {result.query_analysis.research_topics.map((topic) => (
            <Badge key={topic}>{topic}</Badge>
          ))}
        </div>
      </div>
      <div className="panel-soft rounded-lg p-4">
        <h3 className="mb-3 font-semibold">Search Plan</h3>
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
    <article className="rounded-lg border border-[var(--border)] bg-[var(--surface-raised)] p-5 shadow-sm transition duration-200 hover:border-[var(--primary)]">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge>rank {paper.rank}</Badge>
            <Badge>{paper.paper.year}</Badge>
            {paper.paper.venue ? <Badge>{paper.paper.venue}</Badge> : null}
            <Badge>{formatScore(paper.relevance_score)}</Badge>
            <Badge>{paper.category}</Badge>
          </div>
          <h4 className="text-lg font-bold leading-snug">{paper.paper.title}</h4>
          <p className="mt-2 text-sm text-[var(--muted)]">{paper.paper.authors.join(", ")}</p>
        </div>
      </div>

      <p className="text-sm leading-6 text-[var(--muted-strong)]">{paper.paper.abstract}</p>

      <div className="mt-4 rounded-md border border-[var(--border)] bg-[var(--surface)] p-3">
        <p className="text-sm font-semibold">Ranking reason</p>
        <p className="mt-1 text-sm text-[var(--muted)]">{paper.ranking_reason}</p>
      </div>

      {paper.evidence.length ? (
        <div className="mt-4 space-y-2">
          <p className="text-sm font-semibold">Evidence</p>
          {paper.evidence.map((item) => (
            <div key={`${item.source}-${item.text}`} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge>{item.source}</Badge>
                <Badge>{formatScore(item.confidence)}</Badge>
              </div>
              <p className="text-[var(--muted)]">{item.text}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        {paper.paper.sources.map((source) => (
          <Badge key={source}>{source}</Badge>
        ))}
      </div>

      {identifiers.length ? (
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {identifiers.map(([label, value]) => (
            <div key={`${label}-${value}`} className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-xs">
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
            className="inline-flex min-h-10 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-semibold text-[var(--primary)] transition duration-200 hover:border-[var(--primary)]"
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            Landing page
          </a>
        ) : null}
        {paper.paper.urls.pdf ? (
          <a
            href={paper.paper.urls.pdf}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-10 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-semibold text-[var(--primary)] transition duration-200 hover:border-[var(--primary)]"
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
      <h3 className="mb-3 font-semibold">Method Clusters</h3>
      <div className="space-y-3">
        {result.method_clusters.map((cluster) => (
          <div key={cluster.name} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3">
            <p className="font-semibold">{cluster.name}</p>
            <p className="mt-1 text-sm text-[var(--muted)]">{cluster.summary}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {cluster.paper_ranks.map((rank) => (
                <Badge key={rank}>rank {rank}</Badge>
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
      <h3 className="mb-3 font-semibold">Timeline</h3>
      <div className="space-y-3">
        {result.timeline.map((item) => (
          <div key={item.year} className="rounded-md border border-[var(--border)] bg-[var(--surface)] p-3">
            <p className="metric-value text-lg font-bold text-[var(--primary)]">{item.year}</p>
            <p className="mt-1 text-sm text-[var(--muted)]">{item.summary}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {item.paper_ranks.map((rank) => (
                <Badge key={rank}>rank {rank}</Badge>
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
      <h3 className="mb-3 font-semibold">Missing Evidence</h3>
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
        创建 search run 后，这里会展示高度相关论文、部分相关论文、方法聚类、时间线和证据缺口。
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
