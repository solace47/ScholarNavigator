import type {
  HealthResponse,
  InternalSearchPreviewRequest,
  RuntimeConfigResponse,
  SearchRunCreateRequest,
  SearchRunCreateResponse,
  SearchRunResultResponse,
  SearchRunStatusResponse,
  StreamEvent,
} from "@/types/api";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const STREAM_EVENTS = [
  "run_started",
  "stage_started",
  "stage_completed",
  "connector_completed",
  "judgement_updated",
  "cost_updated",
  "warning",
  "error",
  "run_completed",
] as const;

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch (error) {
    throw new ApiError(
      "后端服务不可用，请先启动 FastAPI Mock API",
      undefined,
    );
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail ?? body?.error?.message ?? detail;
    } catch {
      // Keep the HTTP status text.
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>("/api/v1/health");
}

export function getRuntimeConfig(): Promise<RuntimeConfigResponse> {
  return requestJson<RuntimeConfigResponse>("/api/v1/runtime/config");
}

export function createSearchRun(
  payload: SearchRunCreateRequest,
): Promise<SearchRunCreateResponse> {
  return requestJson<SearchRunCreateResponse>("/api/v1/search/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getSearchRun(runId: string): Promise<SearchRunStatusResponse> {
  return requestJson<SearchRunStatusResponse>(`/api/v1/search/runs/${runId}`);
}

export function getSearchRunResult(runId: string): Promise<SearchRunResultResponse> {
  return requestJson<SearchRunResultResponse>(`/api/v1/search/runs/${runId}/result`);
}

export function createRealSearchRun(
  payload: SearchRunCreateRequest,
): Promise<SearchRunCreateResponse> {
  return requestJson<SearchRunCreateResponse>("/api/v1/real/search/runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getRealSearchRun(runId: string): Promise<SearchRunStatusResponse> {
  return requestJson<SearchRunStatusResponse>(`/api/v1/real/search/runs/${runId}`);
}

export function getRealSearchRunResult(
  runId: string,
): Promise<SearchRunResultResponse> {
  return requestJson<SearchRunResultResponse>(
    `/api/v1/real/search/runs/${runId}/result`,
  );
}

export function cancelRealSearchRun(runId: string): Promise<SearchRunStatusResponse> {
  return requestJson<SearchRunStatusResponse>(
    `/api/v1/real/search/runs/${runId}/cancel`,
    {
      method: "POST",
    },
  );
}

export function previewRealSearchApiResult(
  payload: InternalSearchPreviewRequest,
): Promise<SearchRunResultResponse> {
  return requestJson<SearchRunResultResponse>("/api/v1/internal/search/preview/api-result", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function streamEvents(
  path: string,
  onEvent: (event: StreamEvent) => void,
  onTransportError: (message: string) => void,
): () => void {
  const source = new EventSource(`${API_BASE_URL}${path}`);

  STREAM_EVENTS.forEach((eventName) => {
    source.addEventListener(eventName, (message) => {
      const event = message as MessageEvent<string>;
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        payload = { raw: event.data };
      }

      onEvent({
        event: eventName,
        payload,
        receivedAt: new Date().toISOString(),
      });

      if (eventName === "run_completed") {
        source.close();
      }
    });
  });

  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      return;
    }
    onTransportError("后端服务不可用，请先启动 FastAPI Mock API");
    source.close();
  };

  return () => source.close();
}

export function streamSearchRunEvents(
  runId: string,
  onEvent: (event: StreamEvent) => void,
  onTransportError: (message: string) => void,
): () => void {
  return streamEvents(
    `/api/v1/search/runs/${runId}/events`,
    onEvent,
    onTransportError,
  );
}

export function streamRealSearchRunEvents(
  runId: string,
  onEvent: (event: StreamEvent) => void,
  onTransportError: (message: string) => void,
): () => void {
  return streamEvents(
    `/api/v1/real/search/runs/${runId}/events`,
    onEvent,
    onTransportError,
  );
}
