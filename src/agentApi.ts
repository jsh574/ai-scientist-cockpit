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
export const usesRealAgents = import.meta.env.VITE_ENABLE_REAL_AGENTS !== "false";

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
  model_policy?: {
    supported_fields: string[];
    dify_supported_fields: string[];
    dify_unsupported_fields: string[];
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

export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "pausing"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "human_review"
  | "retry"
  | "interrupted"
  | "failed"
  | "completed";

export interface WorkflowRun {
  schema_version: "workflow_run_v1";
  run_id: string;
  task_id: string;
  iteration_id: number;
  status: WorkflowRunStatus;
  start_stage: StageId;
  current_node: string;
  current_stage: StageId;
  cancel_requested: boolean;
  pause_requested: boolean;
  sequence: number;
  node_results: Array<{
    node_id: string;
    stage: StageId;
    status: string;
    completed_at: string;
  }>;
  pending_instructions: Array<{
    instruction_id: string;
    comment: string;
    target_stage: StageId | null;
    action: "append" | "pause_modify";
    status: string;
    created_at: string;
  }>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  finished_at: string | null;
}

export interface NodeRunSummary {
  schema_version: "node_run_v1";
  node_run_id: string;
  workflow_run_id: string | null;
  task_id: string;
  node_id: StageId;
  stage: StageId;
  iteration: number;
  status: string;
  error?: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface NodeRunDetail {
  metadata: NodeRunSummary;
  input: Record<string, unknown>;
  output: AgentResponse | null;
  review: ReviewRecord | null;
}

export interface TaskStageHistoryEntry extends TaskStageDetail {
  iteration: number;
  node_run_id: string;
  started_at: string;
  finished_at: string | null;
}

export interface NodeValidation {
  task_id: string;
  node_id: StageId;
  valid: boolean;
  missing_fields: string[];
  input: Record<string, unknown>;
  would_invalidate: string[];
}

export interface ControllerRoute {
  schema_version: "controller_route_v1";
  intent: "explain" | "modify" | "rerun_agent" | "compare_versions" | "retrieve_more" | "cancel" | "status_query";
  target_stage: StageId | null;
  reason: string;
  optimized_instruction: string;
  answer: string;
}

export interface IterationPlan {
  schema_version: "iteration_plan_v1";
  problem_type: string;
  agents_to_rerun: StageId[];
  artifacts_to_keep: string[];
  artifacts_to_invalidate: string[];
  instructions_by_agent: Partial<Record<StageId, string>>;
  must_regenerate_plan: boolean;
  reason: string;
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
  if (!usesRealAgents) return context;
  const result = await requestJson<{ task_id: string; task_context: TaskContext }>("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: context.mode,
      original_question: context.user_input.original_question,
      user_constraints: context.user_input.user_constraints,
      model_policy: context.model_policy,
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

export async function fetchTaskStageHistory(taskId: string): Promise<TaskStageHistoryEntry[]> {
  const result = await requestJson<{ history: TaskStageHistoryEntry[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/stage-history`,
  );
  return result.history;
}

export async function startWorkflowRun(
  taskId: string,
  startStage: StageId = "question_understanding",
  feedback?: string,
): Promise<WorkflowRun> {
  const result = await requestJson<{ task_id: string; run: WorkflowRun }>(
    `/api/tasks/${encodeURIComponent(taskId)}/start`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start_stage: startStage, feedback }),
    },
  );
  return result.run;
}

export async function fetchWorkflowRun(runId: string): Promise<WorkflowRun> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}`);
}

export async function fetchTaskRuns(taskId: string): Promise<WorkflowRun[]> {
  const result = await requestJson<{ task_id: string; runs: WorkflowRun[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/runs`,
  );
  return result.runs;
}

export async function fetchNodeRuns(taskId: string, nodeId: StageId): Promise<NodeRunSummary[]> {
  const result = await requestJson<{ runs: NodeRunSummary[] }>(
    `/api/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/runs`,
  );
  return result.runs;
}

export async function fetchNodeRun(
  taskId: string,
  nodeId: StageId,
  nodeRunId: string,
): Promise<NodeRunDetail> {
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/runs/${encodeURIComponent(nodeRunId)}`,
  );
}

export async function validateNodeInput(taskId: string, nodeId: StageId): Promise<NodeValidation> {
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/validate`,
    { method: "POST" },
  );
}

export async function executeNode(
  taskId: string,
  nodeId: StageId,
  mode: "only" | "to" | "from",
  inputOverride: Record<string, unknown>,
): Promise<{ run?: WorkflowRun; result?: StageExecutionResult }> {
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, input_override: inputOverride }),
    },
  );
}

export async function fetchNodeRunDiff(
  taskId: string,
  nodeId: StageId,
  left: string,
  right: string,
): Promise<VersionDiffResult> {
  const query = new URLSearchParams({ left, right });
  return requestJson(
    `/api/tasks/${encodeURIComponent(taskId)}/nodes/${encodeURIComponent(nodeId)}/runs-diff?${query}`,
  );
}

async function controlWorkflowRun(
  runId: string,
  action: "pause" | "resume" | "cancel",
): Promise<WorkflowRun> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/${action}`, {
    method: "POST",
  });
}

export const pauseWorkflowRun = (runId: string) => controlWorkflowRun(runId, "pause");
export const resumeWorkflowRun = (runId: string) => controlWorkflowRun(runId, "resume");
export const cancelWorkflowRun = (runId: string) => controlWorkflowRun(runId, "cancel");

export async function queueRunInstruction(
  runId: string,
  comment: string,
  targetStage?: StageId,
  action: "append" | "pause_modify" = "append",
): Promise<WorkflowRun> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/instructions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comment, target_stage: targetStage, action }),
  });
}

export async function routeControllerMessage(
  taskId: string,
  message: string,
  execute = true,
): Promise<{ route: ControllerRoute; task_context: TaskContext; run: WorkflowRun | null }> {
  return requestJson<{ route: ControllerRoute; task_context: TaskContext; run: WorkflowRun | null }>(
    `/api/tasks/${encodeURIComponent(taskId)}/controller/route`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, execute }),
    },
  );
}

export async function evaluateResearchPlan(
  taskId: string,
  userScore: number,
  comment: string,
): Promise<{ iteration_plan: IterationPlan; task_context: TaskContext; run: WorkflowRun | null }> {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}/plan-evaluations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_score: userScore, comment, execute: true }),
  });
}

export async function finishTaskIteration(taskId: string): Promise<TaskContext> {
  const result = await requestJson<{ task_id: string; task_context: TaskContext }>(
    `/api/tasks/${encodeURIComponent(taskId)}/iterations/finish`,
    { method: "POST" },
  );
  return result.task_context;
}

export function subscribeTaskEvents(
  taskId: string,
  onEvent: (event: EventLog) => void,
  onConnectionChange?: (connected: boolean) => void,
  after = 0,
): () => void {
  const query = new URLSearchParams({ follow: "true", after: String(after) });
  const source = new EventSource(
    `${apiBaseUrl}/api/tasks/${encodeURIComponent(taskId)}/events/stream?${query}`,
  );
  source.onopen = () => onConnectionChange?.(true);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as EventLog);
    } catch {
      return;
    }
  };
  source.onerror = () => onConnectionChange?.(false);
  return () => source.close();
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
  if (!usesRealAgents) {
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
  if (!usesRealAgents) return { status: decision === "accept" ? "passed" : decision };
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
  if (!usesRealAgents) return null;
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
