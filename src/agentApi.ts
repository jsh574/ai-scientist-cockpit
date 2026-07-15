import { createAgentResponse } from "./mockData";
import type { AgentResponse, StageId, TaskContext } from "./types";

const realAgentStages = new Set<StageId>([
  "question_understanding",
  "knowledge_integration",
  "hypothesis_generation",
  "evidence_mapping",
  "research_planning",
]);

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const useRealAgents = import.meta.env.VITE_ENABLE_REAL_AGENTS !== "false";

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

export async function executeStage(
  stage: StageId,
  context: TaskContext,
  feedback?: string,
): Promise<AgentResponse> {
  if (!useRealAgents || !realAgentStages.has(stage)) {
    return createAgentResponse(stage, context.task_id);
  }

  try {
    const response = await fetch(`${apiBaseUrl}/api/stages/${stage}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_context: context, feedback }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : response.statusText;
      throw new Error(`Agent API ${response.status}: ${detail}`);
    }
    return body as AgentResponse;
  } catch (error) {
    return failedResponse(stage, context, error);
  }
}
