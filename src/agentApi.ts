import { createAgentResponse } from "./mockData";
import type {
  AgentResponse,
  EventLog,
  ReviewRecord,
  StageExecutionResult,
  StageId,
  StageStatus,
  TaskContext,
} from "./types";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const useRealAgents = import.meta.env.VITE_ENABLE_REAL_AGENTS !== "false";

export interface HealthStatus {
  status: "ok" | "degraded";
  version: string;
  protocol_version: string;
  model: string;
  max_iterations: number;
  ready_agent_count: number;
  real_agent_stages: string[];
  sources: Record<
    string,
    {
      available?: boolean;
      ready?: boolean;
      credential_required?: boolean;
      credential_configured?: boolean;
      mode?: string;
    }
  >;
  capabilities: Record<string, boolean>;
  attachments: { max_bytes: number; allowed_extensions: string[] };
  llm?: {
    timeout_seconds: number;
    max_retries: number;
    thinking_enabled: boolean;
    knowledge_max_attempts: number;
  };
  mcp: { server: string; transport: string };
}

export interface TaskManifest {
  task_id: string;
  title?: string;
  mode: TaskContext["mode"];
  status: string;
  current_stage: TaskContext["current_stage"];
  iteration: number;
  archived?: boolean;
  attachment_count?: number;
  stage_status?: Partial<Record<StageId, StageStatus | "completed">>;
  created_at: string;
  updated_at: string;
}

export interface TaskRecord {
  manifest: TaskManifest;
  task_context: TaskContext;
}

export interface TaskStageDetail {
  task_id: string;
  stage: { stage: StageId; agent_id: string };
  status: StageStatus | "completed" | "retry";
  input: Record<string, unknown> | null;
  output: AgentResponse | null;
  review: ReviewRecord | null;
}

export interface RemoteAttachment {
  attachment_id: string;
  name: string;
  path: string;
  media_type: string;
  size: number;
  created_at: string;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : response.statusText;
    throw new Error(`API ${response.status}: ${detail}`);
  }
  return body as T;
}

function failedResponse(stage: StageId, context: TaskContext, error: unknown): AgentResponse {
  const message = error instanceof Error ? error.message : String(error);
  return {
    metadata: {
      task_id: context.task_id,
      agent_id: `${stage}_agent_gateway`,
      stage,
      iteration: context.iteration,
      status: "failed",
    },
    payload: {},
    self_review: {
      passed: false,
      overall_score: 0,
      threshold: 0.75,
      dimension_scores: {},
      issues: [message],
      suggestions: ["检查后端服务、Agent 路径、Python 依赖和模型密钥后重试。"],
    },
  };
}

export async function createTask(context: TaskContext): Promise<TaskContext> {
  if (!useRealAgents) return context;
  const result = await requestJson<{ task_id: string; task_context: TaskContext }>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: context.mode,
      original_question: context.user_input.original_question,
      user_constraints: context.user_input.user_constraints,
    }),
  });
  return result.task_context;
}

export async function fetchHealthStatus(): Promise<HealthStatus> {
  return requestJson<HealthStatus>("/api/health");
}

export async function fetchTasks(includeArchived = false): Promise<TaskManifest[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  const result = await requestJson<{ tasks: TaskManifest[] }>(`/api/tasks${query}`);
  return result.tasks;
}

export async function fetchTaskRecord(taskId: string): Promise<TaskRecord> {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function fetchTaskStage(taskId: string, stage: StageId): Promise<TaskStageDetail> {
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/stages/${encodeURIComponent(stage)}`,
  );
}

export async function fetchTaskEvents(taskId: string): Promise<EventLog[]> {
  const result = await requestJson<{ events: EventLog[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/events`,
  );
  return result.events;
}

export async function archiveTask(taskId: string, archived = true): Promise<TaskManifest> {
  const result = await requestJson<{ manifest: TaskManifest }>(
    `/api/tasks/${encodeURIComponent(taskId)}/archive`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    },
  );
  return result.manifest;
}

export async function fetchTaskAttachments(taskId: string): Promise<RemoteAttachment[]> {
  const result = await requestJson<{ attachments: RemoteAttachment[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/attachments`,
  );
  return result.attachments;
}

export async function uploadTaskAttachments(
  taskId: string,
  files: File[],
): Promise<{ attachments: RemoteAttachment[]; task_context: TaskContext }> {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}/attachments`, {
    method: "POST",
    body: form,
  });
}

export async function executeStage(
  stage: StageId,
  context: TaskContext,
  feedback?: string,
): Promise<StageExecutionResult> {
  if (!useRealAgents) {
    return {
      task_id: context.task_id,
      stage,
      status: "passed",
      response: createAgentResponse(stage, context.task_id),
      review: null,
    };
  }

  try {
    return await requestJson<StageExecutionResult>(
      `/api/tasks/${encodeURIComponent(context.task_id)}/stages/${stage}/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback }),
      },
    );
  } catch (error) {
    return {
      task_id: context.task_id,
      stage,
      status: "failed",
      response: failedResponse(stage, context, error),
      review: null,
    };
  }
}

export async function submitHumanReview(
  taskId: string,
  stage: StageId,
  decision: "accept" | "retry" | "rollback",
  comment: string,
): Promise<{ status: string; review?: ReviewRecord; task_context?: TaskContext }> {
  if (!useRealAgents) return { status: decision === "accept" ? "passed" : decision };
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}/reviews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ stage, decision, comment }),
  });
}

export async function recordFeedback(
  taskId: string,
  targetStage: StageId,
  comment: string,
  settings: {
    mode: TaskContext["mode"];
    reasoningLevel: TaskContext["user_input"]["user_constraints"]["reasoning_level"];
    memoryLevel: TaskContext["user_input"]["user_constraints"]["memory_level"];
  },
): Promise<TaskContext | null> {
  if (!useRealAgents) return null;
  const result = await requestJson<{ task_context: TaskContext }>(
    `/api/tasks/${encodeURIComponent(taskId)}/feedback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_stage: targetStage,
        comment,
        rerun_downstream: false,
        execute: false,
        mode: settings.mode,
        reasoning_level: settings.reasoningLevel,
        memory_level: settings.memoryLevel,
      }),
    },
  );
  return result.task_context;
}

export async function fetchTaskContext(taskId: string): Promise<TaskContext> {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}/context`);
}

export interface RemoteArtifact {
  path: string;
  size: number;
  updated_at: number;
}

export async function fetchArtifacts(taskId: string): Promise<RemoteArtifact[]> {
  const result = await requestJson<{ artifacts: RemoteArtifact[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/artifacts`,
  );
  return result.artifacts;
}

export async function fetchVersions(taskId: string) {
  const result = await requestJson<{ versions: TaskContext["versions"] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/versions`,
  );
  return result.versions;
}

export interface VersionDiffResult {
  left: string;
  right: string;
  change_count: number;
  changes: Array<{ path: string; before: unknown; after: unknown }>;
}

export async function fetchVersionDiff(taskId: string, left: string, right: string): Promise<VersionDiffResult> {
  const query = new URLSearchParams({ left, right });
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/versions/diff?${query.toString()}`,
  );
}

export async function exportTaskBundle(taskId: string): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/tasks/${encodeURIComponent(taskId)}/export`, {
    method: "POST",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ? String(body.detail) : `Export failed: ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${taskId}.zip`;
  anchor.click();
  URL.revokeObjectURL(url);
}
