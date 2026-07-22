import {
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Activity,
  Archive,
  ArrowDown,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Copy,
  Download,
  FilePlus2,
  FileJson,
  Globe2,
  HelpCircle,
  Loader2,
  LockKeyhole,
  MessageSquareText,
  Moon,
  Paperclip,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Send,
  Server,
  SlidersHorizontal,
  Sparkles,
  Square,
  Sun,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  archiveTask,
  cancelWorkflowRun,
  createTask,
  executeStage,
  evaluateResearchPlan,
  executeNode,
  exportTaskBundle,
  finishTaskIteration,
  fetchArtifacts,
  fetchHealthStatus,
  fetchNodeRun,
  fetchNodeRunDiff,
  fetchNodeRuns,
  fetchTaskAttachments,
  fetchTaskEvents,
  fetchTaskRecord,
  fetchTaskRuns,
  fetchTaskStage,
  fetchTaskStageHistory,
  fetchTasks,
  fetchWorkflowRun,
  fetchVersionDiff,
  fetchVersions,
  pauseWorkflowRun,
  queueRunInstruction,
  recordFeedback,
  routeControllerMessage,
  resumeWorkflowRun,
  startWorkflowRun,
  subscribeTaskEvents,
  submitHumanReview,
  uploadTaskAttachments,
  usesRealAgents,
  validateNodeInput,
  type HealthStatus,
  type NodeRunDetail,
  type NodeRunSummary,
  type NodeValidation,
  type ControllerRoute,
  type IterationPlan,
  type RemoteAttachment,
  type RemoteArtifact,
  type TaskManifest,
  type TaskStageDetail,
  type TaskStageHistoryEntry,
  type VersionDiffResult,
  type WorkflowRun,
} from "./agentApi";
import {
  createInitialContext,
  createInitialStages,
  createReviewRecord,
  createStageInput,
  createVersion,
  apiSpecs,
  manualGateStages,
  mergeStagePayload,
  seedEvents,
  stageMeta,
  stageOrder,
} from "./mockData";
import type { AgentResponse, EventLog, FinalReview, ResearchPlan, ReviewRecord, RunMode, StageId, StageRun, TaskContext, UserInput, VersionRecord } from "./types";

type Language = "zh" | "en";
type PageId = "workbench" | "system" | "docs";
type ReasoningLevel = "low" | "medium" | "high" | "ultra";
type ApprovalMode = "ask" | "assist" | "auto";
type MemoryLevel = "low" | "medium" | "high";
type MessageKind = "user" | "agent" | "controller";
type MenuId = "reasoning" | "approval" | "memory" | null;
type Theme = "light" | "dark";

const activeWorkflowStatuses = new Set([
  "queued",
  "running",
  "pausing",
  "paused",
  "cancelling",
]);
const fallbackAttachmentExtensions = [".txt", ".md", ".csv", ".json", ".pdf", ".docx", ".pptx", ".xlsx"];
const attachmentAccept = [
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".pdf",
  ".docx",
  ".pptx",
  ".xlsx",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/json",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
].join(",");

interface StarterQuestion {
  domain: string;
  domainLabel: Record<Language, string>;
  question: Record<Language, string>;
}

type FlowNode = Node<FlowNodeData, "flowNode">;

interface FlowNodeData extends Record<string, unknown> {
  active: boolean;
  artifactKey?: string;
  detailId?: string;
  iteration?: number;
  kind: "stage" | "artifact" | "detail";
  lang: Language;
  order?: number;
  stage?: StageId;
  status?: StageRun["status"];
  subtitle: string;
  title: string;
}

interface StateTreeDetailNode {
  id: string;
  sourceArtifact: string;
  subtitle: string;
  title: string;
}

interface StateTreeLane {
  height: number;
  id: StageId;
  order: number;
  status: StageRun["status"];
  y: number;
}

interface ThreadMessage {
  id: string;
  kind: MessageKind;
  stage?: StageId;
  body?: string;
  attachments?: RemoteAttachment[];
  activity?: string;
  activityNode?: string;
  activityProgress?: number;
  workflowRunId?: string;
  iteration?: number;
  response?: AgentResponse | null;
  review?: ReviewRecord | null;
  status?: StageRun["status"];
  needsApproval?: boolean;
  retryFromStage?: StageId;
  revisionNote?: string;
  durationMs?: number;
  createdAt: string;
}

interface ChatScrollState {
  isNearBottom: boolean;
  autoFollowEnabled: boolean;
  hasUnreadOutput: boolean;
  isAgentStreaming: boolean;
}

function pendingFileAttachments(files: File[], messageId: string): RemoteAttachment[] {
  const now = new Date().toISOString();
  return files.map((file, index) => ({
    attachment_id: `pending_${messageId}_${index}`,
    name: file.name,
    path: "",
    media_type: file.type || "application/octet-stream",
    size: file.size,
    created_at: now,
    message_id: messageId,
    upload_status: "pending",
    parse_status: "pending",
  }));
}

interface MessageIndexPreview {
  id: string;
  index: number;
  label: string;
  preview: string;
  left: number;
  top: number;
}

interface PickerOption<T extends string> {
  value: T;
  label: string;
  description?: string;
}

interface ProjectSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  context: TaskContext;
  stages: StageRun[];
  events: EventLog[];
  versions: VersionRecord[];
  messages: ThreadMessage[];
  activeStage: StageId;
  reviewStage: StageId | null;
  pendingIndex: number | null;
  questionDraft: string;
  hasSubmittedQuestion: boolean;
  files: File[];
  attachments: RemoteAttachment[];
  feedbackDrafts: Record<string, string>;
  iterationDraft: string;
  iterationScore: number;
}

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

const eventKey = (event: EventLog) => {
  const runId = event.data?.run_id;
  const sequence = event.data?.sequence;
  return runId && sequence ? `${runId}:${sequence}` : event.event_id;
};

function mentionedStage(message: string, fallback: StageId): StageId {
  const lowered = message.toLowerCase();
  const targets: Array<[string, StageId]> = [
    ["@knowledge", "knowledge_integration"],
    ["@hypothesis", "hypothesis_generation"],
    ["@evidence", "evidence_mapping"],
    ["@planning", "research_planning"],
  ];
  return targets.find(([mention]) => lowered.includes(mention))?.[1] ?? fallback;
}

function nodeEventSummary(event: EventLog, language: Language) {
  const payload = event.data?.payload ?? {};
  const counts = [
    ["candidate_sources", language === "zh" ? "候选文献" : "candidates"],
    ["verified_sources", language === "zh" ? "已验证" : "verified"],
    ["completed_plans", language === "zh" ? "已完成计划" : "plans"],
  ]
    .filter(([key]) => typeof payload[key] === "number")
    .map(([key, label]) => `${label} ${String(payload[key])}`);
  const hypothesis = typeof payload.hypothesis_id === "string" ? payload.hypothesis_id : "";
  return [hypothesis, ...counts].filter(Boolean).join(" · ") || event.message;
}

const nodeActivityLabel: Record<string, Record<Language, string>> = {
  query_planning: { zh: "分析研究问题并规划检索词", en: "Analyzing the question and planning queries" },
  source_search: { zh: "检索文献来源", en: "Searching literature sources" },
  source_verify: { zh: "核验文献来源与标识符", en: "Verifying source identifiers" },
  relevance_filter: { zh: "筛选与问题高度相关的文献", en: "Filtering relevant sources" },
  literature_extract: { zh: "提取结构化文献卡片", en: "Extracting literature cards" },
  evidence_extract: { zh: "提取可追溯证据卡片", en: "Extracting traceable evidence" },
  gap_synthesis: { zh: "梳理现有证据中的知识空白", en: "Synthesizing knowledge gaps" },
  quality_review: { zh: "检查知识整合结果质量", en: "Reviewing integration quality" },
  package_select: { zh: "选择假设及其证据包", en: "Selecting hypothesis evidence packages" },
  aggregate: { zh: "汇总并规范化研究计划", en: "Aggregating research plans" },
  operator_instruction: { zh: "应用用户追加指令", en: "Applying the operator instruction" },
  workflow: { zh: "执行总控工作流", en: "Running the controller workflow" },
};

const stageActivityLabel: Record<StageId, Record<Language, string>> = {
  question_understanding: { zh: "理解并结构化研究问题", en: "Structuring the research question" },
  knowledge_integration: { zh: "整合文献、证据与知识空白", en: "Integrating literature and evidence" },
  hypothesis_generation: { zh: "生成候选科学假设", en: "Generating scientific hypotheses" },
  evidence_mapping: { zh: "梳理假设与证据关系", en: "Mapping hypotheses to evidence" },
  research_planning: { zh: "制定可执行研究方案", en: "Building executable research plans" },
  final_review: { zh: "执行总控最终审核", en: "Running the final controller review" },
};

function getNodeActivityLabel(event: EventLog, language: Language) {
  const nodeId = String(event.data?.node_id ?? event.stage ?? "workflow");
  if (nodeId.startsWith("dify_call:")) {
    const hypothesisId = nodeId.slice("dify_call:".length);
    return language === "zh"
      ? `调用研究规划工作流（${hypothesisId}）`
      : `Calling the planning workflow (${hypothesisId})`;
  }
  if (nodeActivityLabel[nodeId]) return nodeActivityLabel[nodeId][language];
  if (event.stage && stageOrder.includes(event.stage as StageId)) {
    const stage = event.stage as StageId;
    return stageActivityLabel[stage][language];
  }
  return language === "zh" ? "处理当前任务" : "Processing the current task";
}

function getWorkflowActivity(event: EventLog, language: Language) {
  const label = getNodeActivityLabel(event, language);
  const summary = nodeEventSummary(event, language);
  const kind = event.data?.kind;
  if (language === "en") return summary || label;
  if (kind === "partial_output") return `${label}，已产生阶段性结果`;
  if (event.type === "node_final_output") return `${label}已完成，正在校验输出`;
  if (kind === "paused") return `${label}已暂停`;
  const payload = event.data?.payload ?? {};
  const counts = [
    typeof payload.candidate_sources === "number" ? `候选文献 ${payload.candidate_sources} 篇` : "",
    typeof payload.verified_sources === "number" ? `已核验 ${payload.verified_sources} 篇` : "",
    typeof payload.completed_plans === "number" ? `已完成计划 ${payload.completed_plans} 份` : "",
  ].filter(Boolean);
  return counts.length ? `${label}，${counts.join("，")}` : `正在${label}`;
}

function workflowMessageStatus(event: EventLog): StageRun["status"] {
  if (event.type === "node_final_output") return "validating";
  if (event.type === "node_human_review") return "human_review";
  if (event.type === "node_retry") return "revision_required";
  if (event.type === "node_failed") return "failed";
  return "running";
}

function messageIteration(message: ThreadMessage) {
  return message.iteration ?? message.response?.metadata.iteration ?? 1;
}

function stageMessageId(taskId: string, iteration: number, stage: StageId) {
  return `stage_${taskId}_i${String(iteration).padStart(3, "0")}_${stage}`;
}

function upsertStageMessage(
  current: ThreadMessage[],
  stage: StageId,
  iteration: number,
  nextMessage: ThreadMessage,
): ThreadMessage[] {
  const next: ThreadMessage[] = [];
  let inserted = false;
  current.forEach((message) => {
    if (
      message.kind !== "user"
      && message.stage === stage
      && messageIteration(message) === iteration
    ) {
      if (!inserted) {
        next.push(nextMessage);
        inserted = true;
      }
      return;
    }
    next.push(message);
  });
  if (!inserted) next.push(nextMessage);
  return next;
}

function applyWorkflowEventToMessages(
  current: ThreadMessage[],
  event: EventLog,
  language: Language,
  iteration: number,
): ThreadMessage[] {
  const runId = event.data?.run_id;
  if (!runId || !event.stage || !stageOrder.includes(event.stage as StageId)) return current;
  const stage = event.stage as StageId;
  const id = stageMessageId(event.task_id, iteration, stage);
  const existing = current.find((message) => message.id === id)
    ?? current.find((message) =>
      message.kind !== "user"
      && message.stage === stage
      && messageIteration(message) === iteration);
  const sameRun = existing?.workflowRunId === runId;
  const status = workflowMessageStatus(event);
  const createdAt = sameRun ? existing.createdAt : event.created_at;
  const durationMs = ["failed", "human_review", "revision_required", "passed"].includes(status)
    ? Math.max(0, Date.parse(event.created_at) - Date.parse(createdAt))
    : sameRun ? existing?.durationMs : undefined;
  const nextMessage: ThreadMessage = {
    ...(existing ?? {
      id,
      kind: stage === "final_review" ? "controller" : "agent",
      stage,
      response: null,
      review: null,
      needsApproval: false,
      createdAt,
    }),
    id,
    iteration,
    response: sameRun ? existing?.response ?? null : null,
    review: sameRun ? existing?.review ?? null : null,
    needsApproval: false,
    createdAt,
    status,
    activity: getWorkflowActivity(event, language),
    activityNode: getNodeActivityLabel(event, language),
    activityProgress: typeof event.data?.progress === "number" ? event.data.progress : existing?.activityProgress,
    workflowRunId: runId,
    durationMs,
  };
  return upsertStageMessage(current, stage, iteration, nextMessage);
}

function applyWorkflowEventsToMessages(
  current: ThreadMessage[],
  events: EventLog[],
  language: Language,
  iteration: number,
) {
  return [...events]
    .sort((left, right) => (left.data?.sequence ?? 0) - (right.data?.sequence ?? 0))
    .reduce((messages, event) => applyWorkflowEventToMessages(messages, event, language, iteration), current);
}

function applyStageDetailToMessages(
  current: ThreadMessage[],
  detail: TaskStageDetail,
  runId: string,
  createdAt: string,
  runIteration: number,
): ThreadMessage[] {
  if (!detail.output) return current;
  const stage = detail.stage.stage;
  const iteration = detail.output.metadata.iteration ?? runIteration;
  const id = stageMessageId(detail.task_id, iteration, stage);
  const existing = current.find((message) => message.id === id)
    ?? current.find((message) =>
      message.kind !== "user"
      && message.stage === stage
      && messageIteration(message) === iteration);
  const status = normalizeRemoteStageStatus(detail.status);
  const nextMessage: ThreadMessage = {
    ...(existing ?? {
      id,
      kind: stage === "final_review" ? "controller" : "agent",
      stage,
      createdAt,
    }),
    id,
    iteration,
    response: detail.output,
    review: detail.review,
    status,
    needsApproval: status === "human_review" && stage !== "final_review",
    retryFromStage: status === "revision_required"
      ? getFeedbackTargetStage(detail.output, stage)
      : undefined,
    durationMs: detail.output.metadata.duration_ms ?? existing?.durationMs,
    activity: undefined,
    activityNode: undefined,
    activityProgress: undefined,
    workflowRunId: runId,
  };
  return upsertStageMessage(current, stage, iteration, nextMessage);
}

const makeMessageId = (prefix: string) => `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;

function getFeedbackTargetStage(response: AgentResponse, fallback: StageId): StageId {
  const validStages = new Set<StageId>(stageOrder);
  const payload = response.payload as Record<string, unknown>;
  const evidenceMap = Array.isArray(payload.evidence_map) ? payload.evidence_map : [];

  for (const item of evidenceMap) {
    if (!item || typeof item !== "object") continue;
    const detailedReview = (item as Record<string, unknown>).detailed_review;
    if (!detailedReview || typeof detailedReview !== "object") continue;
    const review = detailedReview as Record<string, unknown>;
    const verdict = review.verdict;
    const feedback = review.feedback_for_iteration;
    const candidates = [
      verdict && typeof verdict === "object" ? (verdict as Record<string, unknown>).rollback_target : undefined,
      feedback && typeof feedback === "object" ? (feedback as Record<string, unknown>).back_to : undefined,
    ];
    const target = candidates.find((candidate) => typeof candidate === "string" && validStages.has(candidate as StageId));
    if (target) return target as StageId;
  }

  return fallback;
}

function buildSystemRevisionNote(
  response: AgentResponse,
  review: ReviewRecord | null,
  language: Language,
): string {
  const details = [
    ...response.self_review.issues,
    ...response.self_review.suggestions,
    ...(review?.issues ?? []),
  ].filter((value, index, values) => value && values.indexOf(value) === index);
  const fallback = language === "zh"
    ? "提高本阶段输出的完整性、可验证性和下游可用性。"
    : "Improve completeness, testability, and downstream readiness.";
  const prefix = language === "zh" ? "总控自动修订要求：" : "Controller revision request: ";
  return `${prefix}${details.join("；") || fallback}`;
}

const approvalToRunMode: Record<ApprovalMode, RunMode> = {
  ask: "manual",
  assist: "hybrid",
  auto: "auto",
};

const starterQuestions: StarterQuestion[] = [
  {
    domain: "biomedicine",
    domainLabel: { zh: "生命科学", en: "Life science" },
    question: {
      zh: "神经炎症是否会驱动 Tau 病理扩散，并进一步导致阿尔茨海默病认知下降？",
      en: "Does neuroinflammation drive tau pathology spread and subsequent cognitive decline in Alzheimer's disease?",
    },
  },
  {
    domain: "materials_science",
    domainLabel: { zh: "材料科学", en: "Materials" },
    question: {
      zh: "室温超导候选材料中的电子配对机制，可以通过哪些可证伪实验加以区分？",
      en: "Which falsifiable experiments can distinguish electron-pairing mechanisms in room-temperature superconductor candidates?",
    },
  },
  {
    domain: "astronomy",
    domainLabel: { zh: "天文学", en: "Astronomy" },
    question: {
      zh: "矮星系的恒星运动学能否区分冷暗物质与自相互作用暗物质模型？",
      en: "Can stellar kinematics in dwarf galaxies distinguish cold dark matter from self-interacting dark matter?",
    },
  },
  {
    domain: "earth_science",
    domainLabel: { zh: "地球科学", en: "Earth science" },
    question: {
      zh: "海洋热浪如何改变碳汇效率，其关键反馈机制能否通过多源数据验证？",
      en: "How do marine heatwaves alter carbon-sink efficiency, and can the key feedback mechanisms be validated with multi-source data?",
    },
  },
];

const domainOptions = ["general", "biomedicine", "materials_science", "astronomy", "earth_science"] as const;

const domainLabels: Record<Language, Record<(typeof domainOptions)[number], string>> = {
  zh: {
    general: "跨学科",
    biomedicine: "生命科学",
    materials_science: "材料科学",
    astronomy: "天文学",
    earth_science: "地球科学",
  },
  en: {
    general: "Cross-domain",
    biomedicine: "Life science",
    materials_science: "Materials",
    astronomy: "Astronomy",
    earth_science: "Earth science",
  },
};

const stageLabel: Record<Language, Record<StageId, string>> = {
  zh: {
    question_understanding: "问题理解",
    knowledge_integration: "知识整合",
    hypothesis_generation: "假设生成",
    evidence_mapping: "证据梳理",
    research_planning: "研究计划",
    final_review: "总控最终输出",
  },
  en: {
    question_understanding: "Question",
    knowledge_integration: "Knowledge",
    hypothesis_generation: "Hypothesis",
    evidence_mapping: "Evidence",
    research_planning: "Plan",
    final_review: "Final output",
  },
};

const stagePurpose: Record<Language, Record<StageId, string>> = {
  zh: {
    question_understanding: "把原始问题转成可检索、可验证、可迭代的 question_card。",
    knowledge_integration: "整理文献卡片、证据卡片和知识空白。",
    hypothesis_generation: "基于证据和知识空白生成候选科学假设。",
    evidence_mapping: "把候选假设和支持、反对、不确定证据绑定。",
    research_planning: "输出变量、数据、方法、指标、失败判据和反馈任务。",
    final_review: "总控检查完整 task_context，给出最终可交付结果。",
  },
  en: {
    question_understanding: "Turns the raw question into a searchable and testable question card.",
    knowledge_integration: "Builds literature cards, evidence cards, and knowledge gaps.",
    hypothesis_generation: "Generates candidate scientific hypotheses from evidence and gaps.",
    evidence_mapping: "Binds each hypothesis to supporting, opposing, and uncertain evidence.",
    research_planning: "Creates variables, data, methods, metrics, falsification criteria, and feedback tasks.",
    final_review: "Checks the full task context and produces the final controller result.",
  },
};

const artifactLabel: Record<Language, Record<string, string>> = {
  zh: {
    question_card: "科学问题卡",
    literature_cards: "文献卡片",
    evidence_cards: "证据卡片",
    knowledge_gaps: "知识空白",
    hypothesis_cards: "候选假设",
    evidence_map: "证据图谱",
    reviews: "评审结论",
    research_plan: "研究计划",
    final_review: "总控审核",
    versions: "版本快照",
  },
  en: {
    question_card: "Question card",
    literature_cards: "Literature cards",
    evidence_cards: "Evidence cards",
    knowledge_gaps: "Knowledge gaps",
    hypothesis_cards: "Hypotheses",
    evidence_map: "Evidence map",
    reviews: "Review verdict",
    research_plan: "Research plan",
    final_review: "Final review",
    versions: "Version snapshot",
  },
};

const statusLabel: Record<Language, Record<string, string>> = {
  zh: {
    queued: "等待",
    running: "运行中",
    validating: "校验中",
    human_review: "待审批",
    passed: "已通过",
    failed: "失败",
    revision_required: "待选择",
    retrying: "重跑中",
    created: "待开始",
    completed: "已完成",
  },
  en: {
    queued: "Queued",
    running: "Running",
    validating: "Validating",
    human_review: "Needs approval",
    passed: "Passed",
    failed: "Failed",
    revision_required: "Choose action",
    retrying: "Retrying",
    created: "Ready",
    completed: "Done",
  },
};

const copy = {
  zh: {
    appName: "灵光闭环",
    appSub: "EurekaLoop",
    productHint: "多智能体科研总控",
    workbench: "工作台",
    docs: "使用文档",
    language: "中文",
    questionPlaceholder: "从一个科学问题开始",
    addFile: "添加文件等内容",
    noFiles: "未添加文件",
    start: "启动总控",
    running: "运行中",
    newTask: "新建项目",
    projects: "项目",
    newProject: "创建新项目",
    archiveProject: "归档项目",
    lightTheme: "白天模式",
    darkTheme: "黑夜模式",
    switchToLightTheme: "切换到白天模式",
    switchToDarkTheme: "切换到黑夜模式",
    stateTree: "状态树",
    fullTree: "完整可视化状态树",
    close: "退出",
    progress: "进度",
    iteration: "迭代轮次",
    currentStage: "当前阶段",
    reasoning: "推理",
    approval: "访问权限",
    memory: "记忆",
    low: "低",
    medium: "中",
    high: "高",
    ultra: "超高",
    ask: "请求批准",
    assist: "替我审批",
    auto: "完全自动",
    approveContinue: "批准继续",
    revise: "提交修改并重跑",
    revisePlaceholder: "写下你不满意的地方，系统会把这段意见交给当前模块重新输出。",
    json: "查看 JSON",
    gatePassed: "总控校验通过，已写回 task_context。",
    gateWaiting: "总控需要你确认后再进入下一阶段。",
    retryQueued: "已收到修改意见，正在重跑当前模块。",
    userQuestion: "科学问题",
    userRevision: "修改建议",
    controllerStarted: "总控已创建任务，并按当前策略调度各模块。",
    emptyThread: "等待新的科研问题。",
    docsTitle: "EurekaLoop 使用文档",
    docsLead: "工作台连接真实总控 API，任务、审核、反馈、版本和 Artifact 都由服务端持久化。",
    doc1: "输入一个科学问题，左下角 + 可以附加文件或背景材料。",
    doc2: "在输入框下方选择推理强度、访问权限和记忆能力。",
    doc3: "启动后，每个模块都会在对话记录中输出自己的结果。",
    doc4: "需要审批时，按钮出现在对应模块消息的结尾；不满意就写修改意见并重跑。",
    doc5: "总控最终输出会作为最后一条控制器消息出现，可打开 JSON 追踪完整结构。",
    backendTitle: "运行契约",
    backendText: "任务通过 /api/tasks 创建，各阶段由 Review Gate 校验；反馈、版本快照、事件和导出结果写入任务隔离的 Artifact 目录。",
  },
  en: {
    appName: "EurekaLoop",
    appSub: "灵光闭环",
    productHint: "Multi-agent research controller",
    workbench: "Workbench",
    docs: "Guide",
    language: "English",
    questionPlaceholder: "Start with a scientific question",
    addFile: "Add files or context",
    noFiles: "No files attached",
    start: "Start controller",
    running: "Running",
    newTask: "New project",
    projects: "Projects",
    newProject: "New project",
    archiveProject: "Archive project",
    lightTheme: "Light theme",
    darkTheme: "Dark theme",
    switchToLightTheme: "Switch to light theme",
    switchToDarkTheme: "Switch to dark theme",
    stateTree: "State tree",
    fullTree: "Full visual state tree",
    close: "Close",
    progress: "Progress",
    iteration: "Iteration",
    currentStage: "Current stage",
    reasoning: "Reasoning",
    approval: "Access",
    memory: "Memory",
    low: "Low",
    medium: "Medium",
    high: "High",
    ultra: "Ultra",
    ask: "Ask approval",
    assist: "Approve for me",
    auto: "Full auto",
    approveContinue: "Approve and continue",
    revise: "Revise and rerun",
    revisePlaceholder: "Describe what should improve. The current module will rerun with this feedback.",
    json: "View JSON",
    gatePassed: "Controller validation passed and wrote the payload into task_context.",
    gateWaiting: "The controller needs your approval before moving on.",
    retryQueued: "Feedback received. The current module is rerunning.",
    userQuestion: "Scientific question",
    userRevision: "Revision note",
    controllerStarted: "The controller created a task and started routing modules with the selected policy.",
    emptyThread: "Waiting for a scientific question.",
    docsTitle: "EurekaLoop Guide",
    docsLead: "The workbench is connected to the live controller API. Tasks, reviews, feedback, versions, and artifacts are persisted by the server.",
    doc1: "Enter a scientific question. Use + to attach files or background context.",
    doc2: "Choose reasoning, access, and memory from the controls below the composer.",
    doc3: "After start, every module writes its output into the conversation thread.",
    doc4: "Approval buttons appear at the end of the related module message; add feedback and rerun if needed.",
    doc5: "The final controller output appears as the last controller message, with JSON available for tracing.",
    backendTitle: "Runtime contract",
    backendText: "Tasks are created through /api/tasks and every stage is validated by the Review Gate. Feedback, snapshots, events, and exports are stored in task-isolated artifact directories.",
  },
};

function makeProjectId(index: number) {
  return `project_${String(index).padStart(3, "0")}_${Date.now().toString(36)}`;
}

function truncateTitle(value: string, fallback: string) {
  const clean = value.replace(/\s+/g, " ").trim();
  if (!clean) return fallback;
  return clean.length > 22 ? `${clean.slice(0, 22)}...` : clean;
}

function createProjectSession(index: number, mode: RunMode, language: Language, title?: string): ProjectSession {
  const context = createInitialContext(mode);
  const preparedContext: TaskContext = {
    ...context,
    user_input: {
      ...context.user_input,
      user_constraints: {
        ...context.user_input.user_constraints,
        language: language === "zh" ? "zh-CN" : "en-US",
      },
    },
  };
  const now = new Date().toISOString();
  return {
    id: makeProjectId(index),
    title: title ?? (language === "zh" ? `项目${index}` : `Project ${index}`),
    createdAt: now,
    updatedAt: now,
    context: preparedContext,
    stages: createInitialStages(preparedContext),
    events: seedEvents,
    versions: [],
    messages: [],
    activeStage: "question_understanding",
    reviewStage: null,
    pendingIndex: null,
    questionDraft: "",
    hasSubmittedQuestion: false,
    files: [],
    attachments: [],
    feedbackDrafts: {},
    iterationDraft: "",
    iterationScore: 3,
  };
}

function normalizeRemoteStageStatus(status: TaskStageDetail["status"]): StageRun["status"] {
  if (status === "completed") return "passed";
  if (
    status === "queued" ||
    status === "running" ||
    status === "validating" ||
    status === "human_review" ||
    status === "passed" ||
    status === "failed" ||
    status === "revision_required" ||
    status === "retrying"
  ) {
    return status;
  }
  return status === "retry" ? "revision_required" : "queued";
}

function invalidateStageRuns(stages: StageRun[], targetIndex: number, nextContext: TaskContext) {
  return stages.map((stage, index) =>
    index < targetIndex
      ? stage
      : {
          ...stage,
          status: index === targetIndex ? "retrying" as const : "queued" as const,
          duration: "0.0s",
          input: createStageInput(stage.id, nextContext),
          output: null,
          review: null,
        },
  );
}

function projectMessagesFromRemote(
  context: TaskContext,
  details: TaskStageDetail[],
  history: TaskStageHistoryEntry[],
  language: Language,
  createdAt: string,
): ThreadMessage[] {
  const messages: ThreadMessage[] = [
    {
      id: `remote_user_${context.task_id}`,
      kind: "user",
      body: context.user_input.original_question,
      createdAt,
    },
    {
      id: `remote_controller_${context.task_id}`,
      kind: "controller",
      body: copy[language].controllerStarted,
      status: "passed",
      createdAt,
    },
  ];
  for (const event of context.feedback_events ?? []) {
    const stage = event.target?.stage;
    if (!stage || !stageOrder.includes(stage)) continue;
    messages.push({
      id: `remote_feedback_${event.feedback_id}`,
      kind: "user",
      stage,
      body: event.input_summary,
      createdAt: event.created_at ?? createdAt,
    });
  }
  const historicalDetails: TaskStageHistoryEntry[] = history.length
    ? history
    : details.flatMap((detail) => detail.output
      ? [{
          ...detail,
          iteration: detail.output.metadata.iteration ?? context.iteration,
          node_run_id: `legacy_${detail.stage.stage}`,
          started_at: createdAt,
          finished_at: detail.review?.created_at ?? createdAt,
        }]
      : []);
  historicalDetails.forEach((detail) => {
    if (!detail.output) return;
    const status = normalizeRemoteStageStatus(detail.status);
    messages.push({
      id: stageMessageId(context.task_id, detail.iteration, detail.stage.stage),
      kind: detail.stage.stage === "final_review" ? "controller" : "agent",
      stage: detail.stage.stage,
      iteration: detail.iteration,
      response: detail.output,
      review: detail.review,
      status,
      needsApproval: status === "human_review" && detail.stage.stage !== "final_review",
      durationMs: detail.output.metadata.duration_ms ?? undefined,
      createdAt: detail.finished_at ?? detail.review?.created_at ?? detail.started_at,
    });
  });
  (context.plan_evaluations ?? []).forEach((rawEvaluation, index) => {
    const evaluation = rawEvaluation as Record<string, unknown>;
    const iterationPlan = (context.iteration_plans?.[index] ?? {}) as Record<string, unknown>;
    const timestamp = String(evaluation.created_at ?? iterationPlan.created_at ?? createdAt);
    const score = Number(evaluation.user_score ?? 0);
    const comment = String(evaluation.comment ?? "");
    const agents = Array.isArray(iterationPlan.agents_to_rerun)
      ? iterationPlan.agents_to_rerun.map(String)
      : [];
    messages.push(
      {
        id: `remote_iteration_user_${context.task_id}_${index}`,
        kind: "user",
        body: language === "zh" ? `计划评分 ${score}/5：${comment}` : `Plan score ${score}/5: ${comment}`,
        createdAt: timestamp,
      },
      {
        id: `remote_iteration_controller_${context.task_id}_${index}`,
        kind: "controller",
        body: language === "zh"
          ? `总控已进入第 ${index + 2} 轮，将重新运行：${agents.map((stage) => stageLabel.zh[stage as StageId] ?? stage).join("、")}。${String(iterationPlan.reason ?? "")}`
          : `The controller started iteration ${index + 2} and will rerun: ${agents.join(", ")}. ${String(iterationPlan.reason ?? "")}`,
        status: "passed",
        createdAt: timestamp,
      },
    );
  });
  (context.controller_routes ?? []).forEach((rawRoute, index) => {
    const route = rawRoute as Record<string, unknown>;
    const timestamp = String(route.created_at ?? createdAt);
    messages.push(
      {
        id: `remote_qa_user_${context.task_id}_${index}`,
        kind: "user",
        body: String(route.optimized_instruction ?? ""),
        createdAt: timestamp,
      },
      {
        id: `remote_qa_controller_${context.task_id}_${index}`,
        kind: "controller",
        body: String(route.answer ?? route.reason ?? ""),
        status: "passed",
        createdAt: timestamp,
      },
    );
  });
  const iterationControl = context.extensions?.iteration_control;
  if (iterationControl?.status === "ended") {
    messages.push({
      id: `remote_iteration_ended_${context.task_id}`,
      kind: "controller",
      body: language === "zh"
        ? "本项目已结束迭代。你现在可以继续针对假设、证据和研究计划向总控提问。"
        : "Iteration has ended. You can continue asking the controller about hypotheses, evidence, and the research plan.",
      status: "passed",
      createdAt: String(iterationControl.ended_at ?? createdAt),
    });
  }
  return messages.sort((left, right) => Date.parse(left.createdAt) - Date.parse(right.createdAt));
}

async function restoreRemoteProject(
  manifest: TaskManifest,
  index: number,
  language: Language,
): Promise<ProjectSession> {
  const [record, events, attachments, details, history] = await Promise.all([
    fetchTaskRecord(manifest.task_id),
    fetchTaskEvents(manifest.task_id),
    fetchTaskAttachments(manifest.task_id),
    Promise.all(stageOrder.map((stage) => fetchTaskStage(manifest.task_id, stage))),
    fetchTaskStageHistory(manifest.task_id),
  ]);
  const context = record.task_context;
  const baseStages = createInitialStages(context);
  const stages = baseStages.map((stage) => {
    const detail = details.find((item) => item.stage.stage === stage.id);
    if (!detail) return stage;
    return {
      ...stage,
      status: normalizeRemoteStageStatus(detail.status),
      input: detail.input ?? stage.input,
      output: detail.output,
      review: detail.review,
      duration: detail.output?.metadata.duration_ms
        ? `${(detail.output.metadata.duration_ms / 1000).toFixed(1)}s`
        : stage.duration,
    };
  });
  const waitingIndex = stages.findIndex((stage) =>
    ["human_review", "revision_required"].includes(stage.status),
  );
  const currentStage = stageOrder.includes(context.current_stage as StageId)
    ? (context.current_stage as StageId)
    : waitingIndex >= 0
      ? stages[waitingIndex].id
      : context.current_stage === "completed"
        ? "final_review"
        : "question_understanding";
  const title = truncateTitle(
    manifest.title || context.user_input.original_question,
    language === "zh" ? `项目${index}` : `Project ${index}`,
  );
  return {
    id: manifest.task_id,
    title,
    createdAt: manifest.created_at,
    updatedAt: manifest.updated_at,
    context,
    stages,
    events: [...events].reverse(),
    versions: context.versions,
    messages: projectMessagesFromRemote(context, details, history, language, manifest.created_at),
    activeStage: currentStage,
    reviewStage: waitingIndex >= 0 ? stages[waitingIndex].id : null,
    pendingIndex: waitingIndex >= 0 ? waitingIndex : null,
    questionDraft: "",
    hasSubmittedQuestion: true,
    files: [],
    attachments,
    feedbackDrafts: {},
    iterationDraft: "",
    iterationScore: 3,
  };
}

function deriveProjectTitle(project: ProjectSession, messages: ThreadMessage[], questionDraft: string) {
  const firstQuestion = messages.find((message) => message.kind === "user" && !message.stage)?.body;
  if (firstQuestion) return truncateTitle(firstQuestion, project.title);
  if (project.messages.length === 0 && questionDraft.trim()) return truncateTitle(questionDraft, project.title);
  return project.title;
}

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "light";
  const saved = window.localStorage.getItem("eurekaloop-theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function App() {
  const [language, setLanguage] = useState<Language>("zh");
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [page, setPage] = useState<PageId>("workbench");
  const [reasoning, setReasoning] = useState<ReasoningLevel>("ultra");
  const [approval, setApproval] = useState<ApprovalMode>("assist");
  const [memory, setMemory] = useState<MemoryLevel>("medium");
  const [openMenu, setOpenMenu] = useState<MenuId>(null);
  const [projects, setProjects] = useState<ProjectSession[]>(() => [createProjectSession(1, "hybrid", "zh")]);
  const [activeProjectId, setActiveProjectId] = useState(() => projects[0].id);
  const [context, setContext] = useState<TaskContext>(() => projects[0].context);
  const [stages, setStages] = useState<StageRun[]>(() => projects[0].stages);
  const [events, setEvents] = useState<EventLog[]>(() => projects[0].events);
  const [versions, setVersions] = useState<VersionRecord[]>(() => projects[0].versions);
  const [messages, setMessages] = useState<ThreadMessage[]>(() => projects[0].messages);
  const [activeStage, setActiveStage] = useState<StageId>(() => projects[0].activeStage);
  const [running, setRunning] = useState(false);
  const [reviewStage, setReviewStage] = useState<StageId | null>(() => projects[0].reviewStage);
  const [pendingIndex, setPendingIndex] = useState<number | null>(() => projects[0].pendingIndex);
  const [treeOpen, setTreeOpen] = useState(false);
  const [jsonOpen, setJsonOpen] = useState<{ title: string; data: unknown } | null>(null);
  const [questionDraft, setQuestionDraft] = useState(() => projects[0].questionDraft);
  const [hasSubmittedQuestion, setHasSubmittedQuestion] = useState(() => projects[0].hasSubmittedQuestion);
  const [files, setFiles] = useState<File[]>(() => projects[0].files);
  const [attachments, setAttachments] = useState<RemoteAttachment[]>(() => projects[0].attachments);
  const [feedbackTarget, setFeedbackTarget] = useState<StageId>("research_planning");
  const [feedbackDrafts, setFeedbackDrafts] = useState<Record<string, string>>(() => projects[0].feedbackDrafts);
  const [iterationDraft, setIterationDraft] = useState(() => projects[0].iterationDraft);
  const [iterationScore, setIterationScore] = useState(() => projects[0].iterationScore);
  const [iterationBusy, setIterationBusy] = useState(false);
  const [remoteArtifacts, setRemoteArtifacts] = useState<RemoteArtifact[]>([]);
  const [runtimeError, setRuntimeError] = useState("");
  const [versionDiff, setVersionDiff] = useState<VersionDiffResult | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [activeRun, setActiveRun] = useState<WorkflowRun | null>(null);
  const [runsByTask, setRunsByTask] = useState<Record<string, WorkflowRun | null>>({});
  const [pendingActionIds, setPendingActionIds] = useState<Set<string>>(() => new Set());
  const [composerDragActive, setComposerDragActive] = useState(false);
  const [chatScrollState, setChatScrollState] = useState<ChatScrollState>({
    isNearBottom: true,
    autoFollowEnabled: true,
    hasUnreadOutput: false,
    isAgentStreaming: false,
  });
  const [streamConnected, setStreamConnected] = useState(false);
  const threadAreaRef = useRef<HTMLElement | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const taskIdRef = useRef(context.task_id);
  const activeProjectIdRef = useRef(activeProjectId);
  const runsByTaskRef = useRef<Record<string, WorkflowRun | null>>({});
  const pendingActionIdsRef = useRef<Set<string>>(new Set());
  const chatScrollStateRef = useRef<ChatScrollState>({
    isNearBottom: true,
    autoFollowEnabled: true,
    hasUnreadOutput: false,
    isAgentStreaming: false,
  });
  const streamConnectedByTaskRef = useRef<Record<string, boolean>>({});
  const restoredLanguageRef = useRef<Language | null>(null);
  const restoredTaskIdsRef = useRef<Set<string>>(new Set());

  const trackWorkflowRun = useCallback((taskId: string, run: WorkflowRun | null) => {
    runsByTaskRef.current = { ...runsByTaskRef.current, [taskId]: run };
    setRunsByTask((current) => ({ ...current, [taskId]: run }));
    if (taskIdRef.current === taskId) {
      setActiveRun(run);
      setRunning(Boolean(run && activeWorkflowStatuses.has(run.status)));
    }
  }, []);

  const t = copy[language];
  const runMode = approvalToRunMode[approval];
  const completedCount = stages.filter((stage) => stage.status === "passed").length;
  const finished = context.current_stage === "completed";
  const iterationEnded = context.extensions?.iteration_control?.status === "ended";
  const controllerFinalReview = context.final_review
    ?? stages.find((stage) => stage.id === "final_review")?.output?.payload.final_review as FinalReview | undefined;
  const iterationReviewReady = Boolean(
    controllerFinalReview && context.research_plan && !iterationEnded && !running,
  );
  const progress = Math.round((completedCount / stages.length) * 100);
  const currentStageLabel = finished ? statusLabel[language].completed : stageLabel[language][activeStage];
  const uploadedLabel = [...attachments.map((item) => item.name), ...files.map((file) => file.name)].join(", ") || t.noFiles;
  const composerCanSubmit = hasSubmittedQuestion
    ? Boolean(questionDraft.trim() || files.length)
    : Boolean(questionDraft.trim());
  const latestEvent = events[0]?.message;
  const maxIterations = health?.max_iterations ?? 10;
  const readyAgentCount = health?.ready_agent_count ?? 0;
  const activeProject = projects.find((project) => project.id === activeProjectId) ?? projects[0];
  const liveNodeEvents = useMemo(() => {
    if (!activeRun) return [];
    const seen = new Set<string>();
    return events.filter((event) => {
      if (event.data?.run_id !== activeRun.run_id || !event.data.node_id) return false;
      if (seen.has(event.data.node_id)) return false;
      seen.add(event.data.node_id);
      return true;
    }).slice(0, 6);
  }, [activeRun, events]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem("eurekaloop-theme", theme);
  }, [theme]);

  const updateChatScrollState = useCallback((patch: Partial<ChatScrollState>) => {
    const next = { ...chatScrollStateRef.current, ...patch };
    const previous = chatScrollStateRef.current;
    if (
      next.isNearBottom === previous.isNearBottom
      && next.autoFollowEnabled === previous.autoFollowEnabled
      && next.hasUnreadOutput === previous.hasUnreadOutput
      && next.isAgentStreaming === previous.isAgentStreaming
    ) {
      return;
    }
    chatScrollStateRef.current = next;
    setChatScrollState(next);
  }, []);

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    updateChatScrollState({
      autoFollowEnabled: true,
      hasUnreadOutput: false,
      isNearBottom: true,
    });
    window.requestAnimationFrame(() => {
      threadEndRef.current?.scrollIntoView({ behavior, block: "end" });
    });
  }, [updateChatScrollState]);

  const syncChatScrollPosition = useCallback(() => {
    const area = threadAreaRef.current;
    if (!area) return;
    const distanceToBottom = area.scrollHeight - area.scrollTop - area.clientHeight;
    const isNearBottom = distanceToBottom < 96;
    updateChatScrollState({
      isNearBottom,
      autoFollowEnabled: isNearBottom,
      hasUnreadOutput: isNearBottom ? false : chatScrollStateRef.current.hasUnreadOutput,
    });
  }, [updateChatScrollState]);

  const addPendingFiles = useCallback((selected: File[]) => {
    if (!selected.length) return;
    const allowed = new Set(health?.attachments.allowed_extensions ?? fallbackAttachmentExtensions);
    const maxBytes = health?.attachments.max_bytes ?? 2_000_000;
    const invalid = selected.find((file) => {
      const dot = file.name.lastIndexOf(".");
      const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
      return !allowed.has(extension) || file.size > maxBytes;
    });
    if (invalid) {
      setRuntimeError(language === "zh"
        ? `无法添加 ${invalid.name}：仅支持 ${[...allowed].join("、")}，单个文件不超过 ${formatBytes(maxBytes)}。`
        : `Cannot add ${invalid.name}. Use ${[...allowed].join(", ")} files up to ${formatBytes(maxBytes)} each.`);
      return;
    }
    setRuntimeError("");
    setFiles((current) => {
      const known = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      return [...current, ...selected.filter((file) => !known.has(`${file.name}:${file.size}:${file.lastModified}`))];
    });
  }, [health?.attachments.allowed_extensions, health?.attachments.max_bytes, language]);

  const removePendingFile = useCallback((target: File) => {
    setFiles((current) => current.filter((file) => file !== target));
  }, []);

  const refreshRuntimeData = useCallback(async () => {
    if (!hasSubmittedQuestion) return;
    setRuntimeError("");
    try {
      const [artifactList, versionList] = await Promise.all([
        fetchArtifacts(context.task_id),
        fetchVersions(context.task_id),
      ]);
      setRemoteArtifacts(artifactList);
      setVersions(versionList);
      if (versionList.length >= 2) {
        const left = versionList[versionList.length - 2].version_id;
        const right = versionList[versionList.length - 1].version_id;
        setVersionDiff(await fetchVersionDiff(context.task_id, left, right));
      } else {
        setVersionDiff(null);
      }
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [context.task_id, hasSubmittedQuestion]);

  useEffect(() => {
    if (page === "system") void refreshRuntimeData();
  }, [page, refreshRuntimeData]);

  useEffect(() => {
    let active = true;
    void fetchHealthStatus()
      .then((result) => {
        if (active) setHealth(result);
      })
      .catch(() => {
        if (active) setHealth(null);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    updateChatScrollState({ isAgentStreaming: running });
  }, [running, updateChatScrollState]);

  useEffect(() => {
    if (!messages.length) return;
    if (chatScrollStateRef.current.autoFollowEnabled || chatScrollStateRef.current.isNearBottom) {
      scrollToLatest("smooth");
      return;
    }
    updateChatScrollState({ hasUnreadOutput: true });
  }, [messages, scrollToLatest, updateChatScrollState]);

  useEffect(() => {
    const now = new Date().toISOString();
    setProjects((current) =>
      current.map((project) =>
        project.id === activeProjectId
          ? {
              ...project,
              activeStage,
              attachments,
              context,
              events,
              feedbackDrafts,
              iterationDraft,
              iterationScore,
              files,
              hasSubmittedQuestion,
              messages,
              pendingIndex,
              questionDraft,
              reviewStage,
              stages,
              title: deriveProjectTitle(project, messages, questionDraft),
              updatedAt: now,
              versions,
            }
          : project,
      ),
    );
  }, [
    activeProjectId,
    activeStage,
    attachments,
    context,
    events,
    feedbackDrafts,
    files,
    hasSubmittedQuestion,
    messages,
    iterationDraft,
    iterationScore,
    pendingIndex,
    questionDraft,
    reviewStage,
    stages,
    versions,
  ]);

  const hydrateProject = useCallback((project: ProjectSession, runOverride?: WorkflowRun | null) => {
    const projectRun = runOverride === undefined
      ? runsByTaskRef.current[project.context.task_id] ?? null
      : runOverride;
    taskIdRef.current = project.context.task_id;
    setContext(project.context);
    setStages(project.stages);
    setEvents(project.events);
    setVersions(project.versions);
    setMessages(project.messages);
    setActiveStage(project.activeStage);
    setReviewStage(project.reviewStage);
    setPendingIndex(project.pendingIndex);
    setQuestionDraft(project.questionDraft);
    setHasSubmittedQuestion(project.hasSubmittedQuestion);
    setFiles(project.files);
    setAttachments(project.attachments);
    const constraints = project.context.user_input.user_constraints;
    setReasoning(constraints.reasoning_level);
    setMemory(constraints.memory_level);
    setApproval(project.context.mode === "manual" ? "ask" : project.context.mode === "auto" ? "auto" : "assist");
    setFeedbackTarget(project.activeStage === "final_review" ? "research_planning" : project.activeStage);
    setFeedbackDrafts(project.feedbackDrafts);
    setIterationDraft(project.iterationDraft);
    setIterationScore(project.iterationScore);
    setIterationBusy(false);
    setRemoteArtifacts([]);
    setRuntimeError("");
    setVersionDiff(null);
    setActiveRun(projectRun);
    setStreamConnected(Boolean(streamConnectedByTaskRef.current[project.context.task_id]));
    setRunning(Boolean(projectRun && activeWorkflowStatuses.has(projectRun.status)));
    setTreeOpen(false);
    setJsonOpen(null);
  }, []);

  useEffect(() => {
    if (restoredLanguageRef.current === language) return;
    let active = true;
    void fetchTasks()
      .then(async (manifests) => {
        const results = await Promise.allSettled(
          manifests.map((manifest, index) => restoreRemoteProject(manifest, index + 1, language)),
        );
        return {
          failedCount: results.filter((result) => result.status === "rejected").length,
          failureDetails: results.flatMap((result) =>
            result.status === "rejected"
              ? [result.reason instanceof Error ? result.reason.message : String(result.reason)]
              : [],
          ),
          remoteProjects: results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []),
        };
      })
      .then(({ failedCount, failureDetails, remoteProjects }) => {
        if (!active) return;
        if (failedCount) {
          const reason = [...new Set(failureDetails)].slice(0, 2).join("; ");
          setRuntimeError(language === "zh"
            ? `${failedCount} 个项目暂时无法恢复：${reason}`
            : `${failedCount} project(s) could not be restored: ${reason}`);
        }
        if (remoteProjects.length === 0) return;
        restoredLanguageRef.current = language;
        restoredTaskIdsRef.current = new Set(remoteProjects.map((project) => project.context.task_id));
        setProjects(remoteProjects);
        setActiveProjectId(remoteProjects[0].id);
        activeProjectIdRef.current = remoteProjects[0].id;
        hydrateProject(remoteProjects[0]);
      })
      .catch((error) => {
        if (active) setRuntimeError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      active = false;
    };
  }, [hydrateProject, language]);

  const selectProject = useCallback(
    (projectId: string) => {
      if (projectId === activeProjectId) return;
      const nextProject = projects.find((project) => project.id === projectId);
      if (!nextProject) return;
      activeProjectIdRef.current = projectId;
      setActiveProjectId(projectId);
      hydrateProject(nextProject);
      setPage("workbench");
    },
    [activeProjectId, hydrateProject, projects],
  );

  const createNewProject = useCallback(() => {
    const nextProject = createProjectSession(projects.length + 1, runMode, language);
    setProjects((current) => [...current, nextProject]);
    activeProjectIdRef.current = nextProject.id;
    setActiveProjectId(nextProject.id);
    hydrateProject(nextProject);
    setPage("workbench");
  }, [hydrateProject, language, projects.length, runMode]);

  const archiveProject = useCallback(async (projectId: string) => {
    const target = projects.find((project) => project.id === projectId);
    if (!target) return;
    const targetRun = runsByTaskRef.current[target.context.task_id];
    if (targetRun && activeWorkflowStatuses.has(targetRun.status)) return;
    setRuntimeError("");
    try {
      if (target.hasSubmittedQuestion) {
        await archiveTask(target.context.task_id);
      }
      const remaining = projects.filter((project) => project.id !== projectId);
      if (remaining.length) {
        setProjects(remaining);
        if (projectId === activeProjectId) {
          activeProjectIdRef.current = remaining[0].id;
          setActiveProjectId(remaining[0].id);
          hydrateProject(remaining[0]);
        }
      } else {
        const fresh = createProjectSession(1, runMode, language);
        setProjects([fresh]);
        activeProjectIdRef.current = fresh.id;
        setActiveProjectId(fresh.id);
        hydrateProject(fresh);
      }
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [activeProjectId, hydrateProject, language, projects, runMode]);

  const appendEvent = useCallback(
    (type: string, messageZh: string, messageEn: string, stage?: StageId | "final_review" | "feedback_revision") => {
      setEvents((current) => [
        {
          event_id: `evt_${String(current.length + 1).padStart(3, "0")}`,
          task_id: taskIdRef.current,
          type,
          stage,
          message: language === "zh" ? messageZh : messageEn,
          created_at: new Date().toISOString(),
        },
        ...current,
      ]);
    },
    [language],
  );

  const pushMessage = useCallback((message: Omit<ThreadMessage, "id" | "createdAt"> & { id?: string }) => {
    const id = message.id ?? makeMessageId(message.kind);
    setMessages((current) => [...current, { ...message, id, createdAt: new Date().toISOString() }]);
    return id;
  }, []);

  const patchMessage = useCallback((id: string, patch: Partial<ThreadMessage>) => {
    setMessages((current) => current.map((message) => (message.id === id ? { ...message, ...patch } : message)));
  }, []);

  const beginMessageAction = useCallback((id: string) => {
    if (pendingActionIdsRef.current.has(id)) return false;
    const next = new Set(pendingActionIdsRef.current);
    next.add(id);
    pendingActionIdsRef.current = next;
    setPendingActionIds(next);
    return true;
  }, []);

  const finishMessageAction = useCallback((id: string) => {
    if (!pendingActionIdsRef.current.has(id)) return;
    const next = new Set(pendingActionIdsRef.current);
    next.delete(id);
    pendingActionIdsRef.current = next;
    setPendingActionIds(next);
  }, []);

  const updateStage = useCallback((stageId: StageId, patch: Partial<StageRun>) => {
    setStages((current) => current.map((stage) => (stage.id === stageId ? { ...stage, ...patch } : stage)));
  }, []);

  const monitorWorkflowRun = useCallback(
    async (initialRun: WorkflowRun): Promise<WorkflowRun> => {
      let currentRun = initialRun;
      const monitoredProjectId = activeProjectIdRef.current;
      const taskId = currentRun.task_id;
      const isForeground = () => activeProjectIdRef.current === monitoredProjectId;
      const updateMonitoredProject = (updater: (project: ProjectSession) => ProjectSession) => {
        setProjects((current) => current.map((project) =>
          project.id === monitoredProjectId || project.context.task_id === taskId
            ? updater(project)
            : project,
        ));
      };
      const loadCompletedStage = (event: EventLog) => {
        if (event.type !== "node_final_output" || !event.stage || !stageOrder.includes(event.stage as StageId)) {
          return;
        }
        const stage = event.stage as StageId;
        void fetchTaskStage(taskId, stage)
          .then((detail) => {
            const status = normalizeRemoteStageStatus(detail.status);
            const durationMs = detail.output?.metadata.duration_ms ?? null;
            updateMonitoredProject((project) => ({
              ...project,
              messages: applyStageDetailToMessages(
                project.messages,
                detail,
                currentRun.run_id,
                event.created_at,
                currentRun.iteration_id,
              ),
              stages: project.stages.map((item) => item.id === stage
                ? {
                    ...item,
                    status,
                    output: detail.output,
                    review: detail.review,
                    duration: durationMs == null ? item.duration : `${(durationMs / 1000).toFixed(1)}s`,
                  }
                : item),
            }));
            if (!isForeground()) return;
            setMessages((current) => applyStageDetailToMessages(
              current,
              detail,
              currentRun.run_id,
              event.created_at,
              currentRun.iteration_id,
            ));
            updateStage(stage, {
              status,
              output: detail.output,
              review: detail.review,
              duration: durationMs == null ? undefined : `${(durationMs / 1000).toFixed(1)}s`,
            });
          })
          .catch((error) => {
            if (isForeground()) {
              setRuntimeError(error instanceof Error ? error.message : String(error));
            }
          });
      };

      trackWorkflowRun(taskId, currentRun);
      const existingEvents = await fetchTaskEvents(currentRun.task_id);
      const currentRunEvents = existingEvents.filter((event) => event.data?.run_id === currentRun.run_id);
      updateMonitoredProject((project) => {
        const known = new Set(project.events.map(eventKey));
        return {
          ...project,
          events: [
            ...existingEvents.filter((event) => !known.has(eventKey(event))).reverse(),
            ...project.events,
          ],
          messages: applyWorkflowEventsToMessages(
            project.messages,
            currentRunEvents,
            language,
            currentRun.iteration_id,
          ),
        };
      });
      if (isForeground()) {
        setEvents((current) => {
          const known = new Set(current.map(eventKey));
          return [
            ...existingEvents.filter((event) => !known.has(eventKey(event))).reverse(),
            ...current,
          ];
        });
        setMessages((current) => applyWorkflowEventsToMessages(
          current,
          currentRunEvents,
          language,
          currentRun.iteration_id,
        ));
      }
      currentRunEvents.forEach(loadCompletedStage);
      const closeStream = subscribeTaskEvents(
        currentRun.task_id,
        (event) => {
          updateMonitoredProject((project) => {
            const nextProject = project.events.some((item) => eventKey(item) === eventKey(event))
              ? project
              : { ...project, events: [event, ...project.events] };
            const nextMessages = applyWorkflowEventToMessages(
              nextProject.messages,
              event,
              language,
              currentRun.iteration_id,
            );
            if (!event.stage || !stageOrder.includes(event.stage as StageId)) {
              return nextMessages === nextProject.messages
                ? nextProject
                : { ...nextProject, messages: nextMessages };
            }
            const stage = event.stage as StageId;
            const status = event.type === "node_started"
              ? "running" as const
              : event.type === "node_final_output"
                ? "validating" as const
                : null;
            return {
              ...nextProject,
              activeStage: stage,
              messages: nextMessages,
              stages: status
                ? nextProject.stages.map((item) => item.id === stage ? { ...item, status } : item)
                : nextProject.stages,
            };
          });
          loadCompletedStage(event);
          if (!isForeground()) return;
          setEvents((current) => current.some((item) => eventKey(item) === eventKey(event))
            ? current
            : [event, ...current]);
          setMessages((current) => applyWorkflowEventToMessages(
            current,
            event,
            language,
            currentRun.iteration_id,
          ));
          if (event.stage && stageOrder.includes(event.stage as StageId)) {
            const stage = event.stage as StageId;
            setActiveStage(stage);
            if (event.type === "node_started") {
              updateStage(stage, { status: "running" });
            } else if (event.type === "node_final_output") {
              updateStage(stage, { status: "validating" });
            }
          }
        },
        (connected) => {
          streamConnectedByTaskRef.current = {
            ...streamConnectedByTaskRef.current,
            [taskId]: connected,
          };
          if (isForeground()) setStreamConnected(connected);
        },
        existingEvents.length,
      );

      try {
        while (activeWorkflowStatuses.has(currentRun.status)) {
          await delay(700);
          currentRun = await fetchWorkflowRun(currentRun.run_id);
          trackWorkflowRun(taskId, currentRun);
          if (stageOrder.includes(currentRun.current_stage)) {
            updateMonitoredProject((project) => ({
              ...project,
              activeStage: currentRun.current_stage,
            }));
            if (isForeground()) setActiveStage(currentRun.current_stage);
          }
        }

        const record = await fetchTaskRecord(currentRun.task_id);
        const restored = await restoreRemoteProject(record.manifest, 1, language);
        const restoredProject = { ...restored, id: monitoredProjectId };
        updateMonitoredProject(() => restoredProject);
        trackWorkflowRun(taskId, currentRun);
        if (isForeground()) {
          hydrateProject(restoredProject, currentRun);
        }
        if (isForeground() && currentRun.status === "failed") {
          setRuntimeError(currentRun.error || "Workflow run failed.");
        }
        return currentRun;
      } finally {
        closeStream();
        streamConnectedByTaskRef.current = {
          ...streamConnectedByTaskRef.current,
          [taskId]: false,
        };
        if (isForeground()) setStreamConnected(false);
      }
    },
    [hydrateProject, language, trackWorkflowRun, updateStage],
  );

  useEffect(() => {
    if (!hasSubmittedQuestion || !restoredTaskIdsRef.current.has(context.task_id)) return;
    restoredTaskIdsRef.current.delete(context.task_id);
    let active = true;
    void fetchTaskRuns(context.task_id)
      .then((runs) => {
        if (!active) return;
        const latestRun = runs[0] ?? null;
        trackWorkflowRun(context.task_id, latestRun);
        if (latestRun && activeWorkflowStatuses.has(latestRun.status)) {
          void monitorWorkflowRun(latestRun).catch((error) => {
            if (active) {
              trackWorkflowRun(context.task_id, null);
              setRuntimeError(error instanceof Error ? error.message : String(error));
            }
          });
        }
      })
      .catch((error) => {
        if (active) setRuntimeError(error instanceof Error ? error.message : String(error));
      });
    return () => {
      active = false;
    };
  }, [context.task_id, hasSubmittedQuestion, monitorWorkflowRun, trackWorkflowRun]);

  const pauseActiveRun = useCallback(async () => {
    if (!activeRun || !["queued", "running"].includes(activeRun.status)) return;
    try {
      trackWorkflowRun(activeRun.task_id, await pauseWorkflowRun(activeRun.run_id));
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [activeRun, trackWorkflowRun]);

  const resumeActiveRun = useCallback(async () => {
    if (!activeRun || !["pausing", "paused", "interrupted", "cancelled"].includes(activeRun.status)) return;
    try {
      const resumed = await resumeWorkflowRun(activeRun.run_id);
      trackWorkflowRun(resumed.task_id, resumed);
      if (!running) {
        setRunning(true);
        await monitorWorkflowRun(resumed);
      }
    } catch (error) {
      setRunning(false);
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [activeRun, monitorWorkflowRun, running, trackWorkflowRun]);

  const cancelActiveRun = useCallback(async () => {
    if (!activeRun || ["cancelled", "completed", "failed"].includes(activeRun.status)) return;
    try {
      trackWorkflowRun(activeRun.task_id, await cancelWorkflowRun(activeRun.run_id));
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [activeRun, trackWorkflowRun]);

  const buildFreshTask = useCallback(
    (question: string) => {
      const base = createInitialContext(runMode);
      return {
        ...base,
        user_input: {
          ...base.user_input,
          original_question: question,
          user_constraints: {
            ...base.user_input.user_constraints,
            ...context.user_input.user_constraints,
            language: language === "zh" ? "zh-CN" : "en-US",
            reasoning_level: reasoning,
            memory_level: memory,
          },
        },
      };
    },
    [context.user_input.user_constraints, language, memory, reasoning, runMode],
  );

  const updateConstraints = useCallback((patch: Partial<UserInput["user_constraints"]>) => {
    setContext((current) => ({
      ...current,
      user_input: {
        ...current.user_input,
        user_constraints: { ...current.user_input.user_constraints, ...patch },
      },
    }));
  }, []);

  const continueFrom = useCallback(
    async (startIndex: number, inputContext: TaskContext, inputVersions: VersionRecord[], revisionNote?: string) => {
      let workingContext = inputContext;
      let workingVersions = inputVersions;

      for (let index = startIndex; index < stageOrder.length; index += 1) {
        const stage = stageOrder[index];
        const stageRevisionNote = index === startIndex ? revisionNote : undefined;
        const input = createStageInput(stage, workingContext);
        const startTime = performance.now();
        const messageId = pushMessage({
          kind: stage === "final_review" ? "controller" : "agent",
          stage,
          iteration: workingContext.iteration,
          status: "running",
          response: null,
          review: null,
          needsApproval: false,
          revisionNote: stageRevisionNote,
        });

        setActiveStage(stage);
        setContext((current) => ({ ...current, current_stage: stage }));
        updateStage(stage, { status: "running", input, output: null, review: null, duration: "0.0s" });
        appendEvent("stage_started", `${stageLabel.zh[stage]}开始执行。`, `${stageLabel.en[stage]} started.`, stage);
        await delay(430);

        const execution = await executeStage(stage, workingContext, stageRevisionNote);
        const response = execution.response;
        const elapsedMs = performance.now() - startTime;
        updateStage(stage, {
          status: "validating",
          output: response,
          duration: `${(elapsedMs / 1000).toFixed(1)}s`,
        });
        patchMessage(messageId, { status: "validating", response, durationMs: elapsedMs });
        appendEvent(
          "agent_output_received",
          `${stageLabel.zh[stage]}返回统一响应。`,
          `${stageLabel.en[stage]} returned a structured response.`,
          stage,
        );
        await delay(360);

        if (response.metadata.status === "failed" || execution.status === "failed") {
          const failedReview = execution.review ?? createReviewRecord(stage, "fail");
          updateStage(stage, { status: "failed", review: failedReview });
          patchMessage(messageId, { status: "failed", review: failedReview, needsApproval: false });
          appendEvent(
            "task_failed",
            `${stageLabel.zh[stage]}执行失败，请检查错误详情后重试。`,
            `${stageLabel.en[stage]} failed. Check the error details and retry.`,
            stage,
          );
          setContext(execution.task_context ?? { ...workingContext, current_stage: stage });
          setRunning(false);
          return;
        }

        const needsHuman =
          execution.status === "human_review" ||
          (!execution.review && (runMode === "manual" || (runMode === "hybrid" && manualGateStages.includes(stage))));
        const review = execution.review ?? createReviewRecord(stage, needsHuman ? "human_review" : "accept");
        updateStage(stage, { status: needsHuman ? "human_review" : "passed", review });
        patchMessage(messageId, {
          status: needsHuman ? "human_review" : "passed",
          review,
          needsApproval: needsHuman,
        });
        appendEvent(
          needsHuman ? "human_review_requested" : "review_gate_passed",
          needsHuman ? `${stageLabel.zh[stage]}需要人工确认。` : `${stageLabel.zh[stage]}通过 Review Gate。`,
          needsHuman ? `${stageLabel.en[stage]} needs approval.` : `${stageLabel.en[stage]} passed the Review Gate.`,
          stage,
        );

        if (needsHuman) {
          if (execution.task_context) {
            workingContext = execution.task_context;
            workingVersions = execution.task_context.versions;
            setVersions(workingVersions);
            setContext(workingContext);
          } else {
            setContext((current) => ({ ...current, current_stage: "human_review" }));
          }
          setReviewStage(stage);
          setPendingIndex(index);
          setRunning(false);
          return;
        }

        if (execution.status === "retry") {
          const retryReview = execution.review ?? createReviewRecord(stage, "retry");
          const retryFromStage = getFeedbackTargetStage(response, stage);
          const nextContext = execution.task_context ?? { ...workingContext, current_stage: stage };
          updateStage(stage, { status: "revision_required", review: retryReview });
          patchMessage(messageId, {
            status: "revision_required",
            review: retryReview,
            needsApproval: false,
            retryFromStage,
          });
          appendEvent(
            "stage_revision_required",
            `${stageLabel.zh[stage]}低于建议质量阈值，请选择继续执行或重新执行。`,
            `${stageLabel.en[stage]} is below the recommended quality threshold. Continue or rerun this stage.`,
            stage,
          );
          workingContext = nextContext;
          workingVersions = nextContext.versions;
          setContext(nextContext);
          setVersions(workingVersions);
          setReviewStage(stage);
          setPendingIndex(index);
          setRunning(false);
          return;
        }

        const previousVersionCount = workingVersions.length;
        if (execution.task_context) {
          workingContext = execution.task_context;
          workingVersions = execution.task_context.versions;
        } else {
          workingContext = mergeStagePayload(workingContext, stage, response);
          workingContext = { ...workingContext, reviews: [...workingContext.reviews, review] };
          const fallbackVersion = createVersion(stage, workingVersions.length);
          workingVersions = [...workingVersions, fallbackVersion];
          workingContext = { ...workingContext, versions: workingVersions };
        }
        setVersions(workingVersions);
        setContext(workingContext);
        const latestVersion = workingVersions.at(-1);
        if (latestVersion && workingVersions.length > previousVersionCount) {
          appendEvent(
            "context_snapshot_created",
            `${latestVersion.version_id} 已保存。`,
            `${latestVersion.version_id} snapshot saved.`,
            stage,
          );
        }
        await delay(260);
      }

      setRunning(false);
      appendEvent("task_completed", "总控最终审核通过，闭环完成。", "Final controller review passed. Loop completed.", "final_review");
    },
    [appendEvent, patchMessage, pushMessage, runMode, updateStage],
  );

  const startDemo = useCallback(async () => {
    const question = questionDraft.trim();
    if (running || !question) return;

    const selectedFiles = [...files];
    const userMessageId = makeMessageId("user");
    const localTask = buildFreshTask(question);
    setRunning(true);
    let fresh: TaskContext;
    try {
      fresh = await createTask(localTask);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setRunning(false);
      pushMessage({
        kind: "controller",
        body: language === "zh" ? `任务启动准备失败：${message}` : `Task preparation failed: ${message}`,
        status: "failed",
      });
      return;
    }
    taskIdRef.current = fresh.task_id;
    setContext(fresh);
    setStages(createInitialStages(fresh));
    setVersions([]);
    setMessages([]);
    setEvents([
      {
        event_id: "evt_seed_001",
        task_id: fresh.task_id,
        type: "task_created",
        message: language === "zh" ? "任务已创建，等待启动总控。" : "Task created. Controller is ready.",
        created_at: new Date().toISOString(),
      },
    ]);
    setReviewStage(null);
    setPendingIndex(null);
    setActiveStage("question_understanding");
    setHasSubmittedQuestion(true);
    setQuestionDraft("");

    pushMessage({
      id: userMessageId,
      kind: "user",
      body: question,
      attachments: pendingFileAttachments(selectedFiles, userMessageId),
    });
    pushMessage({ kind: "controller", body: t.controllerStarted, status: "passed" });

    if (selectedFiles.length) {
      try {
        const uploaded = await uploadTaskAttachments(fresh.task_id, selectedFiles, userMessageId);
        fresh = uploaded.task_context;
        setContext(fresh);
        setAttachments((current) => [...current, ...uploaded.attachments]);
        patchMessage(userMessageId, { attachments: uploaded.attachments });
        setFiles([]);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        setRuntimeError(detail);
        patchMessage(userMessageId, {
          attachments: pendingFileAttachments(selectedFiles, userMessageId).map((attachment) => ({
            ...attachment,
            upload_status: "failed",
            parse_status: "failed",
          })),
        });
        pushMessage({
          kind: "controller",
          body: language === "zh"
            ? `任务已创建，但附件上传失败：${detail}。任务不会重复创建，可检查文件后再次发送。`
            : `The task was created, but attachment upload failed: ${detail}. Fix the files and send again; no duplicate task will be created.`,
          status: "failed",
        });
      }
    }
    appendEvent(
      "task_started",
      `总控已启动：推理 ${reasoning}，权限 ${approval}，记忆 ${memory}。`,
      `Controller started: reasoning ${reasoning}, access ${approval}, memory ${memory}.`,
    );
    try {
      if (usesRealAgents) {
        const run = await startWorkflowRun(fresh.task_id);
        await monitorWorkflowRun(run);
      } else {
        await continueFrom(0, fresh, []);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setRuntimeError(detail);
      setRunning(false);
      pushMessage({
        kind: "controller",
        body: language === "zh" ? `工作流启动失败：${detail}` : `Workflow failed to start: ${detail}`,
        status: "failed",
      });
    }
  }, [appendEvent, approval, buildFreshTask, continueFrom, files, language, memory, monitorWorkflowRun, patchMessage, pushMessage, questionDraft, reasoning, running, t.controllerStarted]);

  const approveReview = useCallback(async (stageOverride?: StageId, sourceMessageId?: string) => {
    const stage = stageOverride ?? reviewStage;
    if (!stage || running) return;
    const approvalId = sourceMessageId ? `${context.task_id}:${stage}:${sourceMessageId}:approve` : undefined;
    if (sourceMessageId && !beginMessageAction(sourceMessageId)) return;
    const resumeIndex = pendingIndex ?? stageOrder.indexOf(stage);
    if (resumeIndex < 0) {
      if (sourceMessageId) finishMessageAction(sourceMessageId);
      return;
    }
    const stageRun = stages.find((item) => item.id === stage);
    if (!stageRun?.output) {
      if (sourceMessageId) finishMessageAction(sourceMessageId);
      return;
    }

    const qualityOverride = stageRun.status === "revision_required";
    const comment = qualityOverride
      ? language === "zh"
        ? "保留当前输出并继续进入下一阶段。"
        : "Keep the current result and continue to the next stage."
      : language === "zh"
        ? "人工审批通过，继续进入下一阶段。"
        : "Human approval granted. Continue.";
    let remoteResult: Awaited<ReturnType<typeof submitHumanReview>>;
    try {
      remoteResult = await submitHumanReview(context.task_id, stage, "accept", comment, approvalId);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      appendEvent("human_review_failed", `审批提交失败：${detail}`, `Review submission failed: ${detail}`, stage);
      if (sourceMessageId) finishMessageAction(sourceMessageId);
      return;
    }
    const approvedReview: ReviewRecord = remoteResult.review ?? {
      ...createReviewRecord(stage, "accept"),
      operator: "human",
      comment,
    };

    updateStage(stage, { status: "passed", review: approvedReview });
    setMessages((current) =>
      current.map((message) =>
        message.stage === stage && (message.needsApproval || message.status === "revision_required")
          ? { ...message, status: "passed", review: approvedReview, needsApproval: false }
          : message,
      ),
    );
    setReviewStage(null);
    setPendingIndex(null);
    appendEvent(
      qualityOverride ? "quality_gate_overridden" : "human_review_approved",
      qualityOverride ? `${stageLabel.zh[stage]}保留当前输出并继续执行。` : `${stageLabel.zh[stage]}已批准。`,
      qualityOverride ? `${stageLabel.en[stage]} kept the current result and continued.` : `${stageLabel.en[stage]} approved.`,
      stage,
    );

    let nextContext = remoteResult.task_context ?? mergeStagePayload(context, stage, stageRun.output);
    if (!remoteResult.task_context) {
      nextContext = { ...nextContext, reviews: [...nextContext.reviews, approvedReview] };
    }
    const nextVersions = remoteResult.task_context?.versions ?? [...versions, createVersion(stage, versions.length)];
    nextContext = { ...nextContext, versions: nextVersions };
    setContext(nextContext);
    setVersions(nextVersions);

    setRunning(true);
    try {
      const runs = await fetchTaskRuns(context.task_id);
      const waitingRun = activeRun?.task_id === context.task_id
        && ["human_review", "retry", "paused", "interrupted"].includes(activeRun.status)
        ? activeRun
        : runs.find((item) => ["human_review", "retry", "paused", "interrupted"].includes(item.status));
      if (waitingRun) {
        const resumed = await resumeWorkflowRun(waitingRun.run_id);
        await monitorWorkflowRun(resumed);
      } else if (usesRealAgents) {
        const nextStage = stageOrder[resumeIndex + 1];
        if (nextStage) {
          const run = await startWorkflowRun(context.task_id, nextStage);
          await monitorWorkflowRun(run);
        } else {
          setRunning(false);
        }
      } else {
        await delay(260);
        await continueFrom(resumeIndex + 1, nextContext, nextVersions);
      }
    } catch (error) {
      setRunning(false);
      setRuntimeError(error instanceof Error ? error.message : String(error));
    } finally {
      if (sourceMessageId) finishMessageAction(sourceMessageId);
    }
  }, [activeRun, appendEvent, beginMessageAction, context, continueFrom, finishMessageAction, language, monitorWorkflowRun, pendingIndex, reviewStage, running, stages, updateStage, versions]);

  const rerunStageWithFeedback = useCallback(
    async (
      stage: StageId,
      sourceMessageId: string,
      systemGenerated = false,
      response: AgentResponse | null = null,
      review: ReviewRecord | null = null,
    ) => {
      if (running) return;
      if (!beginMessageAction(sourceMessageId)) return;
      const index = stageOrder.indexOf(stage);
      if (index < 0) {
        finishMessageAction(sourceMessageId);
        return;
      }
      const note = systemGenerated && response
        ? buildSystemRevisionNote(response, review, language)
        : feedbackDrafts[sourceMessageId]?.trim() || (language === "zh" ? "请重新检查并改进这一阶段输出。" : "Please re-check and improve this stage output.");

      let rerunContext = context;
      try {
        const recorded = await recordFeedback(context.task_id, stage, note, {
          mode: runMode,
          reasoningLevel: reasoning,
          memoryLevel: memory,
        });
        if (recorded) {
          rerunContext = recorded;
          setContext(recorded);
          setVersions(recorded.versions);
        }
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        appendEvent("feedback_record_failed", `反馈保存失败：${detail}`, `Feedback persistence failed: ${detail}`, stage);
        setRuntimeError(detail);
        finishMessageAction(sourceMessageId);
        return;
      }

      if (systemGenerated) {
        pushMessage({
          kind: "controller",
          body: language === "zh"
            ? `${stageLabel.zh[stage]}由总控根据质量审查意见再次自动修订。`
            : `${stageLabel.en[stage]} will be revised automatically from the quality review.`,
        });
      } else {
        pushMessage({ kind: "user", body: note, stage });
      }
      setMessages((current) =>
        current.map((message) =>
          message.id === sourceMessageId
            ? { ...message, status: "retrying", needsApproval: false, revisionNote: note }
            : message,
        ),
      );
      setFeedbackDrafts((current) => ({ ...current, [sourceMessageId]: "" }));
      setReviewStage(null);
      setPendingIndex(null);
      setActiveStage(stage);
      setStages((current) => invalidateStageRuns(current, index, rerunContext));
      appendEvent(
        systemGenerated ? "stage_auto_revision_requested" : "stage_retry_requested",
        systemGenerated
          ? `${stageLabel.zh[stage]}由总控生成修订意见并准备重跑。`
          : `${stageLabel.zh[stage]}收到修改意见，准备重跑。`,
        systemGenerated
          ? `${stageLabel.en[stage]} received controller-generated revision guidance and will rerun.`
          : `${stageLabel.en[stage]} received feedback and will rerun.`,
        stage,
      );

      setRunning(true);
      try {
        const run = await startWorkflowRun(context.task_id, stage, note);
        await monitorWorkflowRun(run);
      } catch (error) {
        setRunning(false);
        setRuntimeError(error instanceof Error ? error.message : String(error));
      } finally {
        finishMessageAction(sourceMessageId);
      }
    },
    [
      appendEvent,
      beginMessageAction,
      context,
      feedbackDrafts,
      finishMessageAction,
      language,
      memory,
      monitorWorkflowRun,
      pushMessage,
      reasoning,
      runMode,
      running,
    ],
  );

  const submitProjectFeedback = useCallback(async () => {
    const typedNote = questionDraft.trim();
    if (running || (!typedNote && !files.length) || !hasSubmittedQuestion || context.current_stage === "human_review") return;
    const targetStage = mentionedStage(typedNote, feedbackTarget);
    const targetIndex = stageOrder.indexOf(targetStage);
    if (targetIndex < 0) return;

    const selectedFiles = [...files];
    const userMessageId = makeMessageId("user");
    const note = typedNote || (language === "zh"
      ? "请结合本次新增附件重新检查并改进该模块输出。"
      : "Re-check and improve this module using the newly attached files.");
    setRuntimeError("");
    pushMessage({
      id: userMessageId,
      kind: "user",
      body: note,
      stage: targetStage,
      attachments: pendingFileAttachments(selectedFiles, userMessageId),
    });
    let rerunContext = context;
    try {
      if (selectedFiles.length) {
        const uploaded = await uploadTaskAttachments(context.task_id, selectedFiles, userMessageId);
        rerunContext = uploaded.task_context;
        setAttachments((current) => [...current, ...uploaded.attachments]);
        patchMessage(userMessageId, { attachments: uploaded.attachments });
        setFiles([]);
      }
      rerunContext = (await recordFeedback(context.task_id, targetStage, note, {
        mode: runMode,
        reasoningLevel: reasoning,
        memoryLevel: memory,
      })) ?? rerunContext;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setRuntimeError(detail);
      if (selectedFiles.length) {
        patchMessage(userMessageId, {
          attachments: pendingFileAttachments(selectedFiles, userMessageId).map((attachment) => ({
            ...attachment,
            upload_status: "failed",
            parse_status: "failed",
          })),
        });
      }
      pushMessage({
        kind: "controller",
        body: language === "zh" ? `反馈保存失败：${detail}` : `Feedback failed: ${detail}`,
        status: "failed",
      });
      return;
    }

    setQuestionDraft("");
    setContext(rerunContext);
    setVersions(rerunContext.versions);
    setReviewStage(null);
    setPendingIndex(null);
    setActiveStage(targetStage);
    setStages((current) => invalidateStageRuns(current, targetIndex, rerunContext));
    appendEvent(
      "project_feedback_submitted",
      `修改意见已发送至${stageLabel.zh[targetStage]}，不会创建新任务。`,
      `Feedback was sent to ${stageLabel.en[targetStage]} without creating a new task.`,
      targetStage,
    );
    setRunning(true);
    try {
      const run = await startWorkflowRun(context.task_id, targetStage, note);
      await monitorWorkflowRun(run);
    } catch (error) {
      setRunning(false);
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [
    appendEvent,
    context,
    feedbackTarget,
    hasSubmittedQuestion,
    files,
    language,
    memory,
    monitorWorkflowRun,
    patchMessage,
    pushMessage,
    questionDraft,
    reasoning,
    runMode,
    running,
  ]);

  const submitRunInstruction = useCallback(async () => {
    const comment = questionDraft.trim();
    if (!activeRun || !comment) return;
    try {
      const targetStage = mentionedStage(comment, feedbackTarget);
      const updated = await queueRunInstruction(
        activeRun.run_id,
        comment,
        targetStage,
      );
      trackWorkflowRun(updated.task_id, updated);
      pushMessage({ kind: "user", body: comment, stage: targetStage });
      pushMessage({
        kind: "controller",
        body: language === "zh"
          ? `指令已排队，将在下一个安全节点边界应用到${stageLabel.zh[targetStage]}。`
          : `Instruction queued for ${stageLabel.en[targetStage]} at the next safe node boundary.`,
        status: "passed",
      });
      setQuestionDraft("");
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [activeRun, feedbackTarget, language, pushMessage, questionDraft, trackWorkflowRun]);

  const continueIteration = useCallback(async () => {
    const controllerSuggestion = controllerFinalReview?.suggestions?.join("\n").trim();
    const comment = iterationDraft.trim() || controllerSuggestion || (language === "zh"
      ? "请根据总控评价继续优化假设、证据和研究计划。"
      : "Continue improving the hypotheses, evidence, and research plan using the controller review.");
    if (iterationBusy || running || !context.research_plan) return;
    setIterationBusy(true);
    setRuntimeError("");
    pushMessage({
      kind: "user",
      body: language === "zh" ? `计划评分 ${iterationScore}/5：${comment}` : `Plan score ${iterationScore}/5: ${comment}`,
    });
    try {
      const result = await evaluateResearchPlan(context.task_id, iterationScore, comment);
      setContext(result.task_context);
      setVersions(result.task_context.versions);
      setIterationDraft("");
      const firstStage = result.iteration_plan.agents_to_rerun[0] ?? "research_planning";
      setActiveStage(firstStage);
      setFeedbackTarget(firstStage === "final_review" ? "research_planning" : firstStage);
      setStages((current) => invalidateStageRuns(
        current,
        Math.max(0, stageOrder.indexOf(firstStage)),
        result.task_context,
      ));
      pushMessage({
        kind: "controller",
        body: language === "zh"
          ? `总控已生成下一轮方案，将依次重新运行：${result.iteration_plan.agents_to_rerun.map((stage) => stageLabel.zh[stage]).join("、")}。${result.iteration_plan.reason}`
          : `The controller will rerun: ${result.iteration_plan.agents_to_rerun.map((stage) => stageLabel.en[stage]).join(", ")}. ${result.iteration_plan.reason}`,
        status: "passed",
      });
      if (result.run) await monitorWorkflowRun(result.run);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    } finally {
      setIterationBusy(false);
    }
  }, [context, controllerFinalReview, iterationBusy, iterationDraft, iterationScore, language, monitorWorkflowRun, pushMessage, running]);

  const endIteration = useCallback(async () => {
    if (iterationBusy || running || !context.research_plan) return;
    setIterationBusy(true);
    setRuntimeError("");
    try {
      const updated = await finishTaskIteration(context.task_id);
      setContext(updated);
      setIterationDraft("");
      pushMessage({
        kind: "controller",
        body: language === "zh"
          ? "本项目已结束迭代。你现在可以在聊天框继续询问假设、证据、研究计划或最终结论，总控只回答问题，不会自动重跑工作流。"
          : "Iteration has ended. Ask about hypotheses, evidence, the research plan, or conclusions; the controller will answer without rerunning the workflow.",
        status: "passed",
      });
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    } finally {
      setIterationBusy(false);
    }
  }, [context, iterationBusy, language, pushMessage, running]);

  const submitControllerQuestion = useCallback(async () => {
    const question = questionDraft.trim();
    if (!question || running) return;
    setQuestionDraft("");
    setRuntimeError("");
    pushMessage({ kind: "user", body: question });
    try {
      const result = await routeControllerMessage(context.task_id, question, false);
      setContext(result.task_context);
      pushMessage({
        kind: "controller",
        body: result.route.answer || result.route.reason,
        status: "passed",
      });
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, [context.task_id, pushMessage, questionDraft, running]);

  const submitComposer = useCallback(() => {
    if (running && activeRun) {
      void submitRunInstruction();
    } else if (iterationEnded) {
      void submitControllerQuestion();
    } else if (hasSubmittedQuestion) {
      void submitProjectFeedback();
    } else {
      void startDemo();
    }
  }, [activeRun, hasSubmittedQuestion, iterationEnded, running, startDemo, submitControllerQuestion, submitProjectFeedback, submitRunInstruction]);

  const activeStageRun = stages.find((stage) => stage.id === activeStage) ?? stages[0];
  const visibleMessages = iterationReviewReady
    ? messages.filter((message) =>
        message.stage !== "final_review" || messageIteration(message) !== context.iteration)
    : messages;

  return (
    <div className="app-shell">
      <aside className="control-rail">
        <div className="brand-row">
          <span className="brand-mark">
            <img
              alt=""
              aria-hidden="true"
              className="brand-logo-light"
              draggable="false"
              src="/brand/eurekaloop-logo-64.png"
            />
            <img
              alt=""
              aria-hidden="true"
              className="brand-logo-dark"
              draggable="false"
              src="/brand/eurekaloop-logo-64-dark.png"
            />
          </span>
          <div>
            <strong>{t.appName}</strong>
            <small>{t.appSub} · {t.productHint}</small>
          </div>
        </div>

        <ProjectPanel
          activeProjectId={activeProject.id}
          onArchiveProject={(projectId) => void archiveProject(projectId)}
          onCreateProject={createNewProject}
          onSelectProject={selectProject}
          projects={projects}
          runsByTask={runsByTask}
          t={t}
        />

        <nav className="rail-nav" aria-label="Primary">
          <button className={page === "workbench" ? "active" : ""} type="button" onClick={() => setPage("workbench")}>
            <MessageSquareText size={16} />
            {t.workbench}
          </button>
          <button className={page === "system" ? "active" : ""} type="button" onClick={() => setPage("system")}>
            <FileJson size={16} />
            {language === "zh" ? "系统" : "System"}
          </button>
          <button className={page === "docs" ? "active" : ""} type="button" onClick={() => setPage("docs")}>
            <HelpCircle size={16} />
            {t.docs}
          </button>
          <button className="language-button" type="button" onClick={() => setLanguage(language === "zh" ? "en" : "zh")}>
            <Globe2 size={15} />
            {language === "zh" ? "EN" : "中文"}
          </button>
          <button
            aria-label={theme === "dark" ? t.switchToLightTheme : t.switchToDarkTheme}
            className="theme-button"
            title={theme === "dark" ? t.switchToLightTheme : t.switchToDarkTheme}
            type="button"
            onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Moon size={15} /> : <Sun size={15} />}
            {theme === "dark" ? t.darkTheme : t.lightTheme}
          </button>
        </nav>

        <section className="branch-panel">
          <div className="section-title">
            <span>{t.stateTree}</span>
            <small>{completedCount}/{stages.length}</small>
          </div>
          <div className="branch-tree" aria-label={t.stateTree}>
            {stages.map((stage, index) => (
              <button
                className={`branch-node ${stage.status} ${stage.id === activeStage ? "active" : ""}`}
                key={stage.id}
                type="button"
                onClick={() => {
                  setActiveStage(stage.id);
                  setFeedbackTarget(stage.id === "final_review" ? "research_planning" : stage.id);
                  setTreeOpen(true);
                }}
              >
                <i />
                <span>{stageLabel[language][stage.id]}</span>
                <small>{statusLabel[language][stage.status]}</small>
                {index < stages.length - 1 ? <b /> : null}
              </button>
            ))}
          </div>
        </section>

        <section className="task-meter" aria-label={language === "zh" ? "任务状态" : "Task status"}>
          <div>
            <span>{t.currentStage}</span>
            <strong>{currentStageLabel}</strong>
          </div>
          <div className="meter-grid">
            <StatusMetric label={t.progress} value={`${progress}%`} />
            <StatusMetric
              label={t.iteration}
              title={language === "zh"
                ? "初始任务为第 1 轮；每次提交反馈并回退重跑时增加 1 轮。"
                : "The initial task is iteration 1; each feedback-driven rerun adds one iteration."}
              value={`${context.iteration}/${maxIterations}`}
            />
          </div>
          {latestEvent ? <p className="latest-event">{latestEvent}</p> : null}
          {activeRun ? (
            <div className="run-monitor">
              <div className="run-monitor-status">
                <span>{language === "zh" ? "工作流" : "Workflow"}</span>
                <strong>{activeRun.status}</strong>
                <i className={streamConnected ? "connected" : "disconnected"} />
              </div>
              <p>
                {activeWorkflowStatuses.has(activeRun.status)
                  ? streamConnected
                    ? language === "zh" ? "实时进度已连接" : "Live progress connected"
                    : language === "zh" ? "正在重连进度流" : "Reconnecting progress stream"
                  : language === "zh" ? "进度监听已停止" : "Progress stream stopped"}
              </p>
              <small title={activeRun.run_id}>{activeRun.run_id}</small>
              {liveNodeEvents.length ? (
                <div className="node-progress-list">
                  {liveNodeEvents.map((event) => (
                    <article className={event.data?.kind === "partial_output" ? "partial" : ""} key={eventKey(event)}>
                      <div>
                        <strong>{event.data?.node_id}</strong>
                        <span>{event.data?.kind}</span>
                      </div>
                      <p>{nodeEventSummary(event, language)}</p>
                      {typeof event.data?.progress === "number" ? (
                        <i><b style={{ width: `${Math.round(event.data.progress * 100)}%` }} /></i>
                      ) : null}
                    </article>
                  ))}
                </div>
              ) : null}
              <div className="run-controls">
                {["queued", "running"].includes(activeRun.status) ? (
                  <button type="button" onClick={() => void pauseActiveRun()}>
                    <Pause size={13} />
                    {language === "zh" ? "暂停" : "Pause"}
                  </button>
                ) : null}
                {["pausing", "paused", "interrupted", "cancelled"].includes(activeRun.status) ? (
                  <button type="button" onClick={() => void resumeActiveRun()}>
                    <Play size={13} />
                    {language === "zh" ? "继续" : "Resume"}
                  </button>
                ) : null}
                {!['cancelled', 'completed', 'failed'].includes(activeRun.status) ? (
                  <button className="danger" type="button" onClick={() => void cancelActiveRun()}>
                    <X size={13} />
                    {language === "zh" ? "取消" : "Cancel"}
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </section>
      </aside>

      <main className="thread-shell">
        {page === "workbench" ? (
          <div className={`workbench-shell ${messages.length ? "" : "no-index"}`}>
            {visibleMessages.length ? <MessageIndexRail language={language} messages={visibleMessages} /> : null}
            <div className="conversation-shell">
              <header className="thread-header">
              <div>
                <p>{t.appSub}</p>
                <h1>{t.appName}</h1>
              </div>
              <div className="thread-header-status">
                <span className={`connection-status ${health?.status ?? "offline"}`}>
                  <Activity size={13} />
                  {health
                    ? language === "zh"
                      ? `${readyAgentCount} 个 Agent · ${health.model}`
                      : `${readyAgentCount} agents · ${health.model}`
                    : language === "zh"
                      ? "后端未连接"
                      : "Backend offline"}
                </span>
                <span className={`state-chip ${activeStageRun.status}`}>{statusLabel[language][activeStageRun.status]}</span>
              </div>
            </header>

            <section
              className="thread-area"
              aria-label={language === "zh" ? "对话记录" : "Conversation"}
              ref={threadAreaRef}
              onScroll={syncChatScrollPosition}
            >
              {messages.length === 0 ? (
                <ResearchStarter
                  constraints={context.user_input.user_constraints}
                  health={health}
                  language={language}
                  maxIterations={maxIterations}
                  modelPolicy={context.model_policy}
                  onConstraintChange={updateConstraints}
                  onModelPolicyChange={(patch) => setContext((current) => ({
                    ...current,
                    model_policy: {
                      ...(current.model_policy ?? {
                        provider: "dashscope",
                        model: health?.model ?? "qwen3.7-max",
                        reasoning,
                        temperature: 0.2,
                        max_tokens: 6144,
                        timeout_seconds: 120,
                        max_retries: 0,
                        response_format: "json_object",
                        thinking_enabled: false,
                      }),
                      ...patch,
                    },
                  }))}
                  onSelectQuestion={(starter) => {
                    setQuestionDraft(starter.question[language]);
                    updateConstraints({ domain_preference: starter.domain });
                  }}
                />
              ) : null}

              <div className="message-list">
                {visibleMessages.map((message) => (
                  <ThreadMessageCard
                    feedbackValue={feedbackDrafts[message.id] ?? ""}
                    key={message.id}
                    language={language}
                    message={message}
                    actionBusy={pendingActionIds.has(message.id)}
                    onApprove={() => message.stage && void approveReview(message.stage, message.id)}
                    onFeedbackChange={(value) => setFeedbackDrafts((current) => ({ ...current, [message.id]: value }))}
                    onOpenJson={(title, data) => setJsonOpen({ title, data })}
                    onRerun={() => {
                      const targetStage = message.retryFromStage ?? message.stage;
                      if (targetStage) {
                        void rerunStageWithFeedback(
                          targetStage,
                          message.id,
                          message.status === "revision_required",
                          message.response ?? null,
                          message.review ?? null,
                        );
                      }
                    }}
                    onSelectStage={(stage) => {
                      setActiveStage(stage);
                      setFeedbackTarget(stage === "final_review" ? "research_planning" : stage);
                    }}
                    running={running}
                    t={t}
                  />
                ))}
                {iterationReviewReady && controllerFinalReview ? (
                  <IterationDecisionCard
                    busy={iterationBusy}
                    comment={iterationDraft}
                    finalReview={controllerFinalReview}
                    iteration={context.iteration}
                    language={language}
                    maxIterations={maxIterations}
                    onCommentChange={setIterationDraft}
                    onContinue={() => void continueIteration()}
                    onEnd={() => void endIteration()}
                    onScoreChange={setIterationScore}
                    score={iterationScore}
                  />
                ) : null}
                <div ref={threadEndRef} />
              </div>
              {messages.length && (!chatScrollState.isNearBottom || chatScrollState.hasUnreadOutput) ? (
                <button
                  className={`scroll-latest-button ${chatScrollState.hasUnreadOutput ? "unread" : ""}`}
                  type="button"
                  onClick={() => scrollToLatest()}
                >
                  <ArrowDown size={15} />
                  {chatScrollState.hasUnreadOutput
                    ? language === "zh" ? "新输出" : "New output"
                    : language === "zh" ? "回到最新" : "Latest"}
                  {chatScrollState.isAgentStreaming ? <i /> : null}
                </button>
              ) : null}
            </section>

            <section className="composer-shell">
              <div
                className={`composer ${composerDragActive ? "drag-active" : ""}`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (event.dataTransfer.types.includes("Files")) setComposerDragActive(true);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (event.dataTransfer.types.includes("Files")) setComposerDragActive(true);
                }}
                onDragLeave={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  const related = event.relatedTarget;
                  if (!(related instanceof globalThis.Node) || !event.currentTarget.contains(related)) {
                    setComposerDragActive(false);
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  setComposerDragActive(false);
                  addPendingFiles(Array.from(event.dataTransfer.files ?? []));
                }}
              >
                {hasSubmittedQuestion && !iterationEnded ? (
                  <label className="feedback-target">
                    <span>{language === "zh" ? "反馈目标" : "Feedback target"}</span>
                    <select
                      aria-label={language === "zh" ? "选择反馈目标模块" : "Select feedback target module"}
                      disabled={context.current_stage === "human_review"}
                      onChange={(event) => setFeedbackTarget(event.target.value as StageId)}
                      value={feedbackTarget}
                    >
                      {stageOrder.map((stage) => (
                        <option key={stage} value={stage}>{stageLabel[language][stage]}</option>
                      ))}
                    </select>
                  </label>
                ) : null}
                <textarea
                  aria-label={!hasSubmittedQuestion ? t.questionPlaceholder : language === "zh" ? "输入修改意见" : "Enter revision feedback"}
                  disabled={context.current_stage === "human_review" || iterationReviewReady}
                  onChange={(event) => setQuestionDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      submitComposer();
                    }
                  }}
                  placeholder={!hasSubmittedQuestion
                    ? t.questionPlaceholder
                    : iterationEnded
                      ? language === "zh"
                        ? "询问总控：假设依据、证据关系、计划细节或最终结论"
                        : "Ask about hypotheses, evidence, plan details, or conclusions"
                      : iterationReviewReady
                        ? language === "zh"
                          ? "请先在上方总控评价卡片选择继续迭代或结束迭代"
                          : "Choose continue or end iteration in the controller review card"
                        : language === "zh"
                          ? "说明希望如何改进当前项目"
                          : "Describe how this project should be improved"}
                  value={questionDraft}
                />
                <div className="composer-footer">
                  <label className="attach-button">
                    <input
                      multiple
                      accept={attachmentAccept}
                      type="file"
                      onChange={(event) => {
                        addPendingFiles(Array.from(event.target.files ?? []));
                        event.currentTarget.value = "";
                      }}
                    />
                    <Plus size={17} />
                    <span className="tooltip">{t.addFile}</span>
                  </label>

                  <span className="file-hint" title={uploadedLabel}>
                    <Paperclip size={14} />
                    {uploadedLabel}
                  </span>

                  <ControllerSettings
                    approval={approval}
                    language={language}
                    memory={memory}
                    model={health?.model ?? null}
                    openMenu={openMenu}
                    reasoning={reasoning}
                    setApproval={(value) => {
                      setApproval(value);
                      setContext((current) => ({ ...current, mode: approvalToRunMode[value] }));
                    }}
                    setMemory={(value) => {
                      setMemory(value);
                      updateConstraints({ memory_level: value });
                    }}
                    setOpenMenu={setOpenMenu}
                    setReasoning={(value) => {
                      setReasoning(value);
                      updateConstraints({ reasoning_level: value });
                    }}
                    t={t}
                  />

                  <button
                    className="send-button"
                    disabled={context.current_stage === "human_review" || iterationReviewReady || (!running && !composerCanSubmit)}
                    type="button"
                    onClick={running ? () => void cancelActiveRun() : submitComposer}
                    title={running
                      ? language === "zh" ? "立即终止工作流" : "Stop workflow immediately"
                      : !hasSubmittedQuestion
                        ? t.start
                        : iterationEnded
                          ? language === "zh" ? "向总控提问" : "Ask controller"
                          : language === "zh" ? "发送反馈并重跑" : "Send feedback and rerun"}
                  >
                    {running ? <Square fill="currentColor" size={15} /> : <Send size={17} />}
                  </button>
                </div>
                {files.length ? (
                  <div className="pending-file-list" aria-label={language === "zh" ? "待发送附件" : "Pending attachments"}>
                    {files.map((file) => (
                      <span key={`${file.name}:${file.size}:${file.lastModified}`}>
                        <Paperclip size={13} />
                        <b title={file.name}>{file.name}</b>
                        <small>{formatBytes(file.size)}</small>
                        <button
                          aria-label={language === "zh" ? `移除 ${file.name}` : `Remove ${file.name}`}
                          type="button"
                          onClick={() => removePendingFile(file)}
                        >
                          <X size={13} />
                        </button>
                      </span>
                    ))}
                    <button className="pending-file-clear" type="button" onClick={() => setFiles([])}>
                      {language === "zh" ? "清空" : "Clear"}
                    </button>
                  </div>
                ) : null}
              </div>
              </section>
            </div>
          </div>
        ) : page === "system" ? (
          <SystemPage
            artifacts={remoteArtifacts}
            attachments={attachments}
            context={context}
            events={events}
            language={language}
            health={health}
            maxIterations={maxIterations}
            onExport={() => void exportTaskBundle(context.task_id).catch((error) => setRuntimeError(String(error)))}
            onRefresh={() => void refreshRuntimeData()}
            runtimeError={runtimeError}
            stages={stages}
            versions={versions}
            versionDiff={versionDiff}
          />
        ) : (
          <DocsPage language={language} t={t} />
        )}
      </main>

      {treeOpen ? (
        <StateTreeModal
          activeStage={activeStage}
          context={context}
          iteration={context.iteration}
          language={language}
          onClose={() => setTreeOpen(false)}
          onSelectStage={(stage) => {
            setActiveStage(stage);
            setFeedbackTarget(stage === "final_review" ? "research_planning" : stage);
          }}
          onNodeAction={(stage, action) => {
            if (action === "history") {
              setTreeOpen(false);
              setPage("system");
              return;
            }
            if (action === "pause") {
              void pauseActiveRun();
              return;
            }
            void executeNode(context.task_id, stage, action === "only" ? "only" : "from", {})
              .then(async (result) => {
                if (result.run) {
                  await monitorWorkflowRun(result.run);
                } else {
                  const record = await fetchTaskRecord(context.task_id);
                  hydrateProject(await restoreRemoteProject(record.manifest, 1, language));
                }
              })
              .catch((error) => setRuntimeError(error instanceof Error ? error.message : String(error)));
          }}
          stages={stages}
          t={t}
        />
      ) : null}

      {jsonOpen ? (
        <JsonModal data={jsonOpen.data} onClose={() => setJsonOpen(null)} title={jsonOpen.title} />
      ) : null}
    </div>
  );
}

function ResearchStarter({
  constraints,
  health,
  language,
  maxIterations,
  modelPolicy,
  onConstraintChange,
  onModelPolicyChange,
  onSelectQuestion,
}: {
  constraints: UserInput["user_constraints"];
  health: HealthStatus | null;
  language: Language;
  maxIterations: number;
  modelPolicy: TaskContext["model_policy"];
  onConstraintChange: (patch: Partial<UserInput["user_constraints"]>) => void;
  onModelPolicyChange: (patch: Partial<NonNullable<TaskContext["model_policy"]>>) => void;
  onSelectQuestion: (starter: StarterQuestion) => void;
}) {
  const zh = language === "zh";
  const selectedDomain = domainOptions.includes(constraints.domain_preference as (typeof domainOptions)[number])
    ? (constraints.domain_preference as (typeof domainOptions)[number])
    : "general";
  const readyAgents = health
    ? Object.entries(health.sources).filter(([stage, source]) => stage !== "artifact_service" && source.ready).length
    : 0;
  const detailOptions: Array<UserInput["user_constraints"]["output_detail_level"]> = ["brief", "standard", "detailed"];
  const detailLabels: Record<Language, Record<UserInput["user_constraints"]["output_detail_level"], string>> = {
    zh: { brief: "精简", standard: "标准", detailed: "详细" },
    en: { brief: "Brief", standard: "Standard", detailed: "Detailed" },
  };

  return (
    <section className="research-starter">
      <header className="starter-heading">
        <div>
          <span>{zh ? "科研起点" : "Research starting point"}</span>
          <h2>{zh ? "代表性科学问题" : "Representative scientific questions"}</h2>
        </div>
        <div className={`starter-connection ${health?.status ?? "offline"}`}>
          <Activity size={14} />
          {health
            ? health.status === "ok"
              ? zh ? "全部 Agent 就绪" : "All agents ready"
              : zh ? `${readyAgents} 个 Agent 就绪` : `${readyAgents} agents ready`
            : zh ? "等待后端" : "Backend offline"}
        </div>
      </header>

      <div className="starter-question-grid">
        {starterQuestions.map((starter) => (
          <button key={starter.domain} type="button" onClick={() => onSelectQuestion(starter)}>
            <span>{starter.domainLabel[language]}</span>
            <strong>{starter.question[language]}</strong>
            <ArrowDown size={15} />
          </button>
        ))}
      </div>

      <div className="starter-bottom-grid">
        <section className="constraint-panel">
          <div className="starter-section-title">
            <SlidersHorizontal size={15} />
            <strong>{zh ? "科研约束" : "Research constraints"}</strong>
          </div>
          <label>
            <span>{zh ? "领域" : "Domain"}</span>
            <select value={selectedDomain} onChange={(event) => onConstraintChange({ domain_preference: event.target.value })}>
              {domainOptions.map((domain) => <option key={domain} value={domain}>{domainLabels[language][domain]}</option>)}
            </select>
          </label>
          <div className="constraint-field">
            <span>{zh ? "输出深度" : "Output detail"}</span>
            <div className="detail-segments">
              {detailOptions.map((detail) => (
                <button
                  className={constraints.output_detail_level === detail ? "active" : ""}
                  key={detail}
                  type="button"
                  onClick={() => onConstraintChange({ output_detail_level: detail })}
                >
                  {detailLabels[language][detail]}
                </button>
              ))}
            </div>
          </div>
          <label>
            <span>{zh ? "最大假设数" : "Max hypotheses"}</span>
            <input
              max={8}
              min={1}
              type="number"
              value={constraints.max_hypotheses}
              onChange={(event) => onConstraintChange({ max_hypotheses: Math.min(8, Math.max(1, Number(event.target.value) || 1)) })}
            />
          </label>
          <details className="model-policy-settings">
            <summary>{zh ? "模型高级设置" : "Advanced model settings"}</summary>
            <label><span>Temperature</span><input min={0} max={2} step={0.1} type="number" value={modelPolicy?.temperature ?? 0.2} onChange={(event) => onModelPolicyChange({ temperature: Number(event.target.value) })} /></label>
            <label><span>Max tokens</span><input min={256} max={131072} step={256} type="number" value={modelPolicy?.max_tokens ?? 6144} onChange={(event) => onModelPolicyChange({ max_tokens: Number(event.target.value) })} /></label>
            <label><span>{zh ? "超时（秒）" : "Timeout (seconds)"}</span><input min={1} max={3600} type="number" value={modelPolicy?.timeout_seconds ?? 120} onChange={(event) => onModelPolicyChange({ timeout_seconds: Number(event.target.value) })} /></label>
            <label className="model-policy-check"><input type="checkbox" checked={modelPolicy?.thinking_enabled ?? false} onChange={(event) => onModelPolicyChange({ thinking_enabled: event.target.checked })} /><span>{zh ? "启用思考模式" : "Enable thinking"}</span></label>
            <p>{(modelPolicy?.max_tokens ?? 6144) >= 8192 || (modelPolicy?.thinking_enabled ?? false) ? (zh ? "高耗时/高成本配置" : "Higher latency/cost configuration") : (zh ? "标准耗时/成本" : "Standard latency/cost")}</p>
            {health?.model_policy?.dify_unsupported_fields.length ? <small>Dify {zh ? "不支持统一下发" : "does not accept"}: {health.model_policy.dify_unsupported_fields.join(", ")}</small> : null}
          </details>
        </section>

        <section className="runtime-summary">
          <div className="starter-section-title">
            <Server size={15} />
            <strong>{zh ? "真实运行环境" : "Live runtime"}</strong>
          </div>
          <dl>
            <div><dt>{zh ? "模型" : "Model"}</dt><dd>{health?.model ?? "--"}</dd></div>
            <div><dt>Agents</dt><dd>{health ? readyAgents : "--"}</dd></div>
            <div><dt>{zh ? "最大轮次" : "Iteration limit"}</dt><dd>{maxIterations}</dd></div>
          </dl>
        </section>
      </div>
    </section>
  );
}

function SystemPage({
  artifacts,
  attachments,
  context,
  events,
  health,
  language,
  maxIterations,
  onExport,
  onRefresh,
  runtimeError,
  stages,
  versions,
  versionDiff,
}: {
  artifacts: RemoteArtifact[];
  attachments: RemoteAttachment[];
  context: TaskContext;
  events: EventLog[];
  health: HealthStatus | null;
  language: Language;
  maxIterations: number;
  onExport: () => void;
  onRefresh: () => void;
  runtimeError: string;
  stages: StageRun[];
  versions: VersionRecord[];
  versionDiff: VersionDiffResult | null;
}) {
  const zh = language === "zh";
  return (
    <section className="system-page">
      <header className="system-header">
        <div>
          <p>
            {zh ? "运行状态" : "Runtime"}
            {health ? ` · ${health.model} · ${health.ready_agent_count}/${health.real_agent_stages.length} Agents` : ""}
          </p>
          <h2>{context.task_id}</h2>
        </div>
        <div className="system-actions">
          <button className="ghost-button" type="button" onClick={onRefresh}>
            <RotateCcw size={15} />
            {zh ? "刷新" : "Refresh"}
          </button>
          <button className="main-action" disabled={context.current_stage === "created"} type="button" onClick={onExport}>
            <Archive size={15} />
            {zh ? "导出任务" : "Export task"}
          </button>
        </div>
      </header>

      {runtimeError ? <p className="runtime-error">{runtimeError}</p> : null}

      <div className="runtime-metrics">
        <StatusMetric label={zh ? "当前阶段" : "Current stage"} value={context.current_stage} />
        <StatusMetric label={zh ? "迭代轮次" : "Iteration"} value={`${context.iteration}/${maxIterations}`} />
        <StatusMetric label={zh ? "版本" : "Versions"} value={String(versions.length)} />
        <StatusMetric label={zh ? "事件" : "Events"} value={String(events.length)} />
      </div>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>{zh ? "Agent 连接状态" : "Agent readiness"}</h3>
          <span>
            {health
              ? `${health.ready_agent_count}/${health.real_agent_stages.length} · timeout ${health.llm?.timeout_seconds ?? "--"}s · retry ${health.llm?.max_retries ?? "--"}`
              : "--"}
          </span>
        </div>
        <div className="runtime-table agent-runtime-table">
          {health ? health.real_agent_stages.map((stageName) => {
            const source = health.sources[stageName] ?? {};
            const state = source.ready ? "ready" : source.available ? "credential" : "offline";
            const detail = source.ready
              ? (zh ? "可执行" : "Ready")
              : source.available && source.credential_required && !source.credential_configured
                ? (zh ? "缺少模型密钥" : "Missing model credential")
                : (zh ? "Agent 源不可用" : "Agent source unavailable");
            return (
              <div className="runtime-row" key={stageName}>
                <strong>{stageLabel[language][stageName as StageId] ?? stageName}</strong>
                <code>{source.mode ?? "--"}</code>
                <span>{detail}</span>
                <b className={`api-state ${state}`}>{state}</b>
              </div>
            );
          }) : <p className="runtime-empty">{zh ? "后端未连接，无法判断 Agent 状态。" : "Backend offline; Agent readiness is unknown."}</p>}
        </div>
      </section>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>{zh ? "阶段与审核" : "Stages and reviews"}</h3>
          <span>Review Gate</span>
        </div>
        <div className="runtime-table stage-runtime-table">
          {stages.map((stage) => (
            <div className="runtime-row" key={stage.id}>
              <strong>{stageLabel[language][stage.id]}</strong>
              <span>{stageMeta[stage.id].agent}</span>
              <code>{stage.status}</code>
              <em>{stage.review ? `${Math.round(stage.review.overall_score * 100)}%` : "--"}</em>
            </div>
          ))}
        </div>
      </section>

      <section className="runtime-section collaboration-timeline">
        <div className="runtime-title"><h3>{zh ? "Agent 协作时间线" : "Agent collaboration timeline"}</h3><span>{events.length}</span></div>
        <div>
          {events.slice(0, 40).map((event) => (
            <article key={eventKey(event)}>
              <i />
              <div><strong>{event.stage ? stageLabel[language][event.stage as StageId] ?? event.stage : "Controller"}</strong><p>{event.message}</p><small>{event.type} · {new Date(event.created_at).toLocaleTimeString()}</small></div>
              {event.data?.node_id ? <code>{event.data.node_id}</code> : null}
            </article>
          ))}
        </div>
      </section>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>{zh ? "已上传附件" : "Uploaded attachments"}</h3>
          <span>{attachments.length}</span>
        </div>
        <div className="runtime-table artifact-runtime-table">
          {attachments.length ? attachments.map((attachment) => (
            <div className="runtime-row" key={attachment.attachment_id}>
              <Paperclip size={15} />
              <code>{attachment.name}</code>
              <span>{attachment.parse_status ?? "completed"} · {attachment.file_type ?? formatBytes(attachment.size)}</span>
            </div>
          )) : <p className="runtime-empty">{zh ? "当前任务还没有持久化附件。" : "This task has no persisted attachments."}</p>}
        </div>
      </section>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>{zh ? "最近版本差异" : "Latest version diff"}</h3>
          <span>{versionDiff ? `${versionDiff.left} → ${versionDiff.right}` : "--"}</span>
        </div>
        <div className="runtime-table diff-runtime-table">
          {versionDiff?.changes.length ? versionDiff.changes.slice(0, 20).map((change) => (
            <div className="runtime-row" key={change.path}>
              <code>{change.path}</code>
              <span>{previewValue(change.before)}</span>
              <strong>→</strong>
              <span>{previewValue(change.after)}</span>
            </div>
          )) : <p className="runtime-empty">{zh ? "至少保存两个版本后显示字段差异。" : "Field changes appear after two snapshots."}</p>}
        </div>
      </section>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>Artifacts</h3>
          <span>{artifacts.length}</span>
        </div>
        <div className="runtime-table artifact-runtime-table">
          {artifacts.length ? artifacts.slice(0, 40).map((artifact) => (
            <div className="runtime-row" key={artifact.path}>
              <FileJson size={15} />
              <code>{artifact.path}</code>
              <span>{formatBytes(artifact.size)}</span>
            </div>
          )) : <p className="runtime-empty">{zh ? "任务运行后显示服务器产物。" : "Server artifacts appear after a task runs."}</p>}
        </div>
      </section>

      <section className="runtime-section">
        <div className="runtime-title">
          <h3>API</h3>
          <span>{apiSpecs.length}</span>
        </div>
        <div className="runtime-table api-runtime-table">
          {apiSpecs.map((api) => (
            (() => {
              const state = !health ? "unknown" : health.capabilities[api.capability] ? "ready" : "offline";
              return (
                <div className="runtime-row" key={`${api.method}-${api.path}`}>
                  <b>{api.method}</b>
                  <code>{api.path}</code>
                  <span className={`api-state ${state}`}>{state}</span>
                </div>
              );
            })()
          ))}
        </div>
      </section>
    </section>
  );
}

function ControllerConsole({
  context,
  language,
  onWorkflowRun,
}: {
  context: TaskContext;
  language: Language;
  onWorkflowRun: (run: WorkflowRun) => void;
}) {
  const zh = language === "zh";
  const [message, setMessage] = useState("");
  const [score, setScore] = useState(3);
  const [route, setRoute] = useState<ControllerRoute | null>(null);
  const [iterationPlan, setIterationPlan] = useState<IterationPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const classify = async () => {
    if (!message.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await routeControllerMessage(context.task_id, message.trim());
      setRoute(result.route);
      if (result.run && !["cancelled", "cancelling"].includes(result.run.status)) {
        onWorkflowRun(result.run);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };
  const iterate = async () => {
    if (!message.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await evaluateResearchPlan(context.task_id, score, message.trim());
      setIterationPlan(result.iteration_plan);
      if (result.run) onWorkflowRun(result.run);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="runtime-section controller-console">
      <div className="runtime-title"><h3>{zh ? "总控助手与计划评价" : "Controller assistant and plan evaluation"}</h3><span>Router</span></div>
      <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder={zh ? "提问、修改要求或计划评价" : "Ask, request a modification, or evaluate the plan"} />
      <div className="controller-console-actions">
        <label><span>{zh ? "计划评分" : "Plan score"}</span><input min={1} max={5} type="number" value={score} onChange={(event) => setScore(Math.min(5, Math.max(1, Number(event.target.value))))} /></label>
        <button className="ghost-button" disabled={busy || !message.trim()} type="button" onClick={() => void classify()}>{zh ? "识别意图" : "Classify"}</button>
        <button className="main-action" disabled={busy || !message.trim() || !context.research_plan} type="button" onClick={() => void iterate()}>{zh ? "评价并开始新一轮" : "Evaluate and iterate"}</button>
      </div>
      {error ? <p className="runtime-error">{error}</p> : null}
      {route ? <div className="controller-decision"><strong>{route.intent}{route.target_stage ? ` → ${route.target_stage}` : ""}</strong><p>{route.reason}</p><small>{route.answer}</small></div> : null}
      {iterationPlan ? <div className="controller-decision"><strong>{iterationPlan.problem_type}</strong><p>{iterationPlan.reason}</p><small>{zh ? "重跑" : "Rerun"}: {iterationPlan.agents_to_rerun.join(" → ")}</small></div> : null}
    </section>
  );
}

function NodeDebugger({ context, language, onWorkflowRun }: { context: TaskContext; language: Language; onWorkflowRun: (run: WorkflowRun) => void }) {
  const zh = language === "zh";
  const [nodeId, setNodeId] = useState<StageId>("question_understanding");
  const [runs, setRuns] = useState<NodeRunSummary[]>([]);
  const [detail, setDetail] = useState<NodeRunDetail | null>(null);
  const [validation, setValidation] = useState<NodeValidation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"only" | "to" | "from">("only");
  const [overrideText, setOverrideText] = useState("{}");
  const [leftRun, setLeftRun] = useState("");
  const [rightRun, setRightRun] = useState("");
  const [runDiff, setRunDiff] = useState<VersionDiffResult | null>(null);

  const loadRuns = useCallback(async () => {
    if (context.current_stage === "created") return;
    setLoading(true);
    setError("");
    try {
      const nextRuns = await fetchNodeRuns(context.task_id, nodeId);
      setRuns(nextRuns);
      setLeftRun((current) => current || nextRuns[1]?.node_run_id || "");
      setRightRun((current) => current || nextRuns[0]?.node_run_id || "");
      setDetail(nextRuns[0]
        ? await fetchNodeRun(context.task_id, nodeId, nextRuns[0].node_run_id)
        : null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [context.current_stage, context.task_id, nodeId]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  const validate = async () => {
    setLoading(true);
    setError("");
    try {
      setValidation(await validateNodeInput(context.task_id, nodeId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  const selectRun = async (run: NodeRunSummary) => {
    setError("");
    try {
      setDetail(await fetchNodeRun(context.task_id, nodeId, run.node_run_id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  const execute = async () => {
    setError("");
    let inputOverride: Record<string, unknown>;
    try {
      const parsed = JSON.parse(overrideText);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Override must be a JSON object");
      inputOverride = parsed as Record<string, unknown>;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return;
    }
    if (!window.confirm(zh ? "执行会生成 operator override，并可能使下游结果失效。继续吗？" : "This creates an operator override and may invalidate downstream results. Continue?")) return;
    setLoading(true);
    try {
      const result = await executeNode(context.task_id, nodeId, mode, inputOverride);
      if (result.run) onWorkflowRun(result.run);
      await loadRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  const compareRuns = async () => {
    if (!leftRun || !rightRun) return;
    setLoading(true);
    try {
      setRunDiff(await fetchNodeRunDiff(context.task_id, nodeId, leftRun, rightRun));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="runtime-section node-debugger">
      <div className="runtime-title">
        <h3>{zh ? "节点历史与校验" : "Node history and validation"}</h3>
        <span>{runs.length} runs</span>
      </div>
      <div className="node-debug-toolbar">
        <select value={nodeId} onChange={(event) => { setNodeId(event.target.value as StageId); setValidation(null); }}>
          {stageOrder.map((stage) => <option key={stage} value={stage}>{stageLabel[language][stage]}</option>)}
        </select>
        <button className="ghost-button" disabled={loading || context.current_stage === "created"} type="button" onClick={() => void validate()}>
          <CheckCircle2 size={14} />
          {zh ? "仅校验输入" : "Validate input"}
        </button>
        <button className="ghost-button" disabled={loading || context.current_stage === "created"} type="button" onClick={() => void loadRuns()}>
          <RotateCcw className={loading ? "spin" : ""} size={14} />
          {zh ? "刷新历史" : "Refresh history"}
        </button>
      </div>
      {error ? <p className="runtime-error">{error}</p> : null}
      {validation ? (
        <div className={`node-validation ${validation.valid ? "valid" : "invalid"}`}>
          <strong>{validation.valid ? (zh ? "输入有效" : "Input valid") : (zh ? "输入不完整" : "Input incomplete")}</strong>
          <p>{validation.missing_fields.length ? `${zh ? "缺少" : "Missing"}: ${validation.missing_fields.join(", ")}` : (zh ? "所有声明字段均可读取。" : "All declared inputs are readable.")}</p>
          <small>{zh ? "重跑将影响" : "A rerun would affect"}: {validation.would_invalidate.join(", ")}</small>
        </div>
      ) : null}
      <div className="node-operator-console">
        <div>
          <select value={mode} onChange={(event) => setMode(event.target.value as "only" | "to" | "from")}>
            <option value="only">{zh ? "仅运行此节点" : "Run only this node"}</option>
            <option value="to">{zh ? "运行到此节点" : "Run to this node"}</option>
            <option value="from">{zh ? "从此节点继续" : "Continue from this node"}</option>
          </select>
          <button className="main-action" disabled={loading} type="button" onClick={() => void execute()}>{zh ? "执行" : "Execute"}</button>
        </div>
        <textarea aria-label="Operator input override JSON" value={overrideText} onChange={(event) => setOverrideText(event.target.value)} />
      </div>
      {runs.length >= 2 ? (
        <div className="node-diff-controls">
          <select value={leftRun} onChange={(event) => setLeftRun(event.target.value)}>{runs.map((run) => <option key={run.node_run_id} value={run.node_run_id}>{run.node_run_id}</option>)}</select>
          <span>→</span>
          <select value={rightRun} onChange={(event) => setRightRun(event.target.value)}>{runs.map((run) => <option key={run.node_run_id} value={run.node_run_id}>{run.node_run_id}</option>)}</select>
          <button className="ghost-button" type="button" onClick={() => void compareRuns()}>{zh ? "比较输出" : "Compare outputs"}</button>
        </div>
      ) : null}
      {runDiff ? <div className="node-run-diff">{runDiff.changes.slice(0, 30).map((change) => <div key={change.path}><code>{change.path}</code><span>{previewValue(change.before)}</span><b>→</b><span>{previewValue(change.after)}</span></div>)}</div> : null}
      <div className="node-debug-grid">
        <div className="node-run-list">
          {runs.length ? runs.map((run) => (
            <button className={detail?.metadata.node_run_id === run.node_run_id ? "active" : ""} key={run.node_run_id} type="button" onClick={() => void selectRun(run)}>
              <strong>{run.node_run_id}</strong>
              <span>i{run.iteration} · {run.status}</span>
              <small>{new Date(run.started_at).toLocaleString()}</small>
            </button>
          )) : <p className="runtime-empty">{zh ? "该节点还没有执行历史。" : "This node has no run history."}</p>}
        </div>
        <div className="node-run-detail">
          {detail ? (
            <>
              <div><strong>{detail.metadata.status}</strong><span>{detail.metadata.workflow_run_id || "standalone"}</span></div>
              <details open><summary>Input</summary><pre>{JSON.stringify(detail.input, null, 2)}</pre></details>
              <details><summary>Output</summary><pre>{JSON.stringify(detail.output, null, 2)}</pre></details>
              <details><summary>Review</summary><pre>{JSON.stringify(detail.review, null, 2)}</pre></details>
            </>
          ) : <p className="runtime-empty">{zh ? "选择一条历史查看输入、输出和评审。" : "Select a run to inspect input, output, and review."}</p>}
        </div>
      </div>
    </section>
  );
}

void ControllerConsole;
void NodeDebugger;

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function previewValue(value: unknown) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "∅";
  return text.length > 90 ? `${text.slice(0, 87)}...` : text;
}

function MessageIndexRail({ language, messages }: { language: Language; messages: ThreadMessage[] }) {
  const [preview, setPreview] = useState<MessageIndexPreview | null>(null);

  const showPreview = (message: ThreadMessage, index: number, element: HTMLButtonElement) => {
    const rect = element.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 32);
    setPreview({
      id: message.id,
      index,
      label: getMessageTitle(message, language),
      preview: getMessageIndexPreview(message, language),
      left: Math.min(rect.right + 12, window.innerWidth - width - 12),
      top: Math.min(Math.max(rect.top - 18, 12), window.innerHeight - 118),
    });
  };

  return (
    <aside className="message-index" aria-label={language === "zh" ? "消息索引" : "Message index"}>
      <div className="index-stack">
        {messages.map((message, index) => {
          const label = getMessageTitle(message, language);
          return (
            <button
              aria-describedby={preview?.id === message.id ? `message-index-preview-${message.id}` : undefined}
              aria-label={label}
              className={`index-tick ${message.kind} ${message.status ?? ""}`}
              key={message.id}
              type="button"
              onBlur={() => setPreview(null)}
              onFocus={(event) => showPreview(message, index, event.currentTarget)}
              onMouseEnter={(event) => showPreview(message, index, event.currentTarget)}
              onMouseLeave={() => setPreview(null)}
              onClick={() => document.getElementById(message.id)?.scrollIntoView({ behavior: "smooth", block: "center" })}
            >
              <span aria-hidden="true" />
            </button>
          );
        })}
      </div>
      {preview && typeof document !== "undefined"
        ? createPortal(
            <div
              className="message-index-preview"
              id={`message-index-preview-${preview.id}`}
              role="tooltip"
              style={{ left: preview.left, top: preview.top }}
            >
              <div>
                <b>{String(preview.index + 1).padStart(2, "0")}</b>
                <strong>{preview.label}</strong>
              </div>
              <p>{preview.preview}</p>
            </div>,
            document.body,
          )
        : null}
    </aside>
  );
}

function ProjectPanel({
  activeProjectId,
  onArchiveProject,
  onCreateProject,
  onSelectProject,
  projects,
  runsByTask,
  t,
}: {
  activeProjectId: string;
  onArchiveProject: (projectId: string) => void;
  onCreateProject: () => void;
  onSelectProject: (projectId: string) => void;
  projects: ProjectSession[];
  runsByTask: Record<string, WorkflowRun | null>;
  t: (typeof copy)[Language];
}) {
  return (
    <section className="project-panel">
      <div className="project-titlebar">
        <div className="project-title-label">{t.projects}</div>
        <div className="project-actions">
          <button
            aria-label={t.newProject}
            className="icon-quiet"
            title={t.newTask}
            type="button"
            onClick={onCreateProject}
          >
            <FilePlus2 size={17} />
            <span aria-hidden="true" className="icon-tooltip">{t.newTask}</span>
          </button>
        </div>
      </div>

      <div className="project-list">
        {projects.map((project) => {
          const projectRun = runsByTask[project.context.task_id];
          const projectRunning = Boolean(projectRun && activeWorkflowStatuses.has(projectRun.status));
          return (
            <div className="project-row" key={project.id}>
              <button
                className={`project-item ${project.id === activeProjectId ? "active" : ""}`}
                type="button"
                onClick={() => onSelectProject(project.id)}
                title={projectRun ? `${project.title} · ${projectRun.status}` : project.title}
              >
                <MessageSquareText size={16} />
                <span className="project-name">{project.title}</span>
                {projectRunning ? (
                  <i
                    aria-label={projectRun?.status ?? t.running}
                    className="project-run-indicator"
                    title={projectRun?.status ?? t.running}
                  />
                ) : null}
              </button>
              <button
                aria-label={`${t.archiveProject}: ${project.title}`}
                className="project-archive-button"
                disabled={projectRunning}
                type="button"
                onClick={() => onArchiveProject(project.id)}
                title={projectRunning ? t.running : t.archiveProject}
              >
                <Archive size={15} />
              </button>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ControllerSettings({
  approval,
  language,
  memory,
  model,
  openMenu,
  reasoning,
  setApproval,
  setMemory,
  setOpenMenu,
  setReasoning,
  t,
}: {
  approval: ApprovalMode;
  language: Language;
  memory: MemoryLevel;
  model: string | null;
  openMenu: MenuId;
  reasoning: ReasoningLevel;
  setApproval: (value: ApprovalMode) => void;
  setMemory: (value: MemoryLevel) => void;
  setOpenMenu: (value: MenuId) => void;
  setReasoning: (value: ReasoningLevel) => void;
  t: (typeof copy)[Language];
}) {
  const reasoningOptions: Array<PickerOption<ReasoningLevel>> = [
    { value: "low", label: t.low },
    { value: "medium", label: t.medium },
    { value: "high", label: t.high },
    { value: "ultra", label: t.ultra },
  ];
  const approvalOptions: Array<PickerOption<ApprovalMode>> = [
    {
      value: "ask",
      label: t.ask,
      description: language === "zh" ? "进入下一层模块输出前始终询问" : "Always ask before moving to the next module output",
    },
    {
      value: "assist",
      label: t.assist,
      description: language === "zh" ? "仅对检测到的风险操作请求批准" : "Ask only for detected risky operations",
    },
    {
      value: "auto",
      label: t.auto,
      description: language === "zh" ? "完全由模型自行审批" : "Let the model approve all steps by itself",
    },
  ];
  const memoryOptions: Array<PickerOption<MemoryLevel>> = [
    { value: "low", label: t.low, description: language === "zh" ? "只保留当前任务必要上下文" : "Keep only essential context" },
    { value: "medium", label: t.medium, description: language === "zh" ? "保留阶段摘要和关键反馈" : "Keep stage summaries and key feedback" },
    { value: "high", label: t.high, description: language === "zh" ? "保留更完整的版本和证据历史" : "Keep fuller version and evidence history" },
  ];

  return (
    <div className="codex-controls">
      <DropdownControl
        className="reasoning-control"
        icon={<Brain size={15} />}
        id="reasoning"
        label={`${model ?? (language === "zh" ? "模型" : "Model")} · ${reasoningOptions.find((option) => option.value === reasoning)?.label ?? ""}`}
        menuLabel={t.reasoning}
        onChange={setReasoning}
        onOpenChange={setOpenMenu}
        open={openMenu === "reasoning"}
        options={reasoningOptions}
        value={reasoning}
      />
      <DropdownControl
        className="memory-control"
        icon={<Sparkles size={15} />}
        id="memory"
        label={`${t.memory} ${memoryOptions.find((option) => option.value === memory)?.label ?? ""}`}
        menuLabel={t.memory}
        onChange={setMemory}
        onOpenChange={setOpenMenu}
        open={openMenu === "memory"}
        options={memoryOptions}
        value={memory}
      />
      <DropdownControl
        className={`access-control access-${approval}`}
        icon={<LockKeyhole size={15} />}
        id="approval"
        label={approvalOptions.find((option) => option.value === approval)?.label ?? t.approval}
        menuLabel={language === "zh" ? "应如何批准操作？" : "How should operations be approved?"}
        onChange={setApproval}
        onOpenChange={setOpenMenu}
        open={openMenu === "approval"}
        options={approvalOptions}
        value={approval}
      />
    </div>
  );
}

function DropdownControl<T extends string>({
  className,
  icon,
  id,
  label,
  menuLabel,
  onChange,
  onOpenChange,
  open,
  options,
  value,
}: {
  className?: string;
  icon: ReactNode;
  id: Exclude<MenuId, null>;
  label: string;
  menuLabel: string;
  onChange: (value: T) => void;
  onOpenChange: (value: MenuId) => void;
  open: boolean;
  options: Array<PickerOption<T>>;
  value: T;
}) {
  return (
    <div className={`dropdown-control ${className ?? ""}`}>
      <button className="dropdown-trigger" type="button" onClick={() => onOpenChange(open ? null : id)}>
        {icon}
        <span>{label}</span>
        <ChevronDown size={15} />
      </button>
      {open ? (
        <div className="dropdown-menu">
          <p>{menuLabel}</p>
          {options.map((option) => (
            <button
              className={value === option.value ? "selected" : ""}
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                onOpenChange(null);
              }}
            >
              <span>
                <strong>{option.label}</strong>
                {option.description ? <small>{option.description}</small> : null}
              </span>
              {value === option.value ? <Check size={17} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function IterationDecisionCard({
  busy,
  comment,
  finalReview,
  iteration,
  language,
  maxIterations,
  onCommentChange,
  onContinue,
  onEnd,
  onScoreChange,
  score,
}: {
  busy: boolean;
  comment: string;
  finalReview: FinalReview;
  iteration: number;
  language: Language;
  maxIterations: number;
  onCommentChange: (value: string) => void;
  onContinue: () => void;
  onEnd: () => void;
  onScoreChange: (value: number) => void;
  score: number;
}) {
  const zh = language === "zh";
  const weaknesses = finalReview.weaknesses ?? [];
  const suggestions = finalReview.suggestions ?? [];
  const dimensionLabels: Record<string, string> = zh
    ? {
        question_clarity: "问题清晰度",
        evidence_quality: "证据质量",
        hypothesis_quality: "假设质量",
        evidence_alignment: "证据映射",
        plan_executability: "计划可执行性",
        reproducibility: "可复现性",
      }
    : {
        question_clarity: "Question clarity",
        evidence_quality: "Evidence quality",
        hypothesis_quality: "Hypothesis quality",
        evidence_alignment: "Evidence alignment",
        plan_executability: "Plan execution",
        reproducibility: "Reproducibility",
      };
  return (
    <article className="thread-message controller-message iteration-decision-message">
      <div aria-hidden="true" className="message-avatar ai-message-avatar">
        <img alt="" className="brand-logo-light" draggable="false" src="/brand/eurekaloop-logo-64.png" />
        <img alt="" className="brand-logo-dark" draggable="false" src="/brand/eurekaloop-logo-64-dark.png" />
      </div>
      <div className="message-bubble iteration-decision-card">
        <header>
          <div>
            <strong>{zh ? `第 ${iteration} 轮总控评价` : `Controller review · iteration ${iteration}`}</strong>
            <small>{zh ? `最多 ${maxIterations} 轮` : `${maxIterations} iterations maximum`}</small>
          </div>
          <span className={`state-chip ${finalReview.passed ? "passed" : "revision_required"}`}>
            {Math.round(finalReview.overall_score * 100)} {zh ? "分" : "%"}
          </span>
        </header>
        {finalReview.dimension_scores ? (
          <div className="controller-dimension-scores">
            {Object.entries(finalReview.dimension_scores).map(([dimension, value]) => (
              <span key={dimension}>
                <small>{dimensionLabels[dimension] ?? dimension}</small>
                <strong>{Math.round(value * 100)}</strong>
              </span>
            ))}
          </div>
        ) : null}
        <div className="controller-review-grid">
          <div>
            <strong>{zh ? "总控结论" : "Controller verdict"}</strong>
            <p>{finalReview.passed
              ? zh ? "本轮结果达到基本要求，可以结束，也可以继续精炼。" : "This round meets the baseline. End or continue refining."
              : zh ? "本轮仍有改进空间，建议结合以下问题继续迭代。" : "This round can improve; continue using the issues below."}</p>
          </div>
          <div>
            <strong>{zh ? "主要问题" : "Main issues"}</strong>
            <ul>{(weaknesses.length ? weaknesses : [zh ? "暂无强制修改项" : "No required changes"]).map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
          <div className="controller-agent-suggestions">
            <strong>{zh ? "总控 Agent 改进建议" : "Controller agent suggestions"}</strong>
            <ul>{(suggestions.length ? suggestions : [zh ? "暂无额外建议" : "No additional suggestions"]).map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        </div>
        <div className="user-plan-score">
          <span>{zh ? "你的评分" : "Your score"}</span>
          <div>{[1, 2, 3, 4, 5].map((value) => <button className={score === value ? "active" : ""} key={value} type="button" onClick={() => onScoreChange(value)}>{value}</button>)}</div>
        </div>
        <textarea
          disabled={busy}
          onChange={(event) => onCommentChange(event.target.value)}
          placeholder={zh ? "写下希望下一轮重点改进的内容；留空则按总控建议继续。" : "Describe what to improve next; leave blank to use controller suggestions."}
          value={comment}
        />
        <div className="iteration-decision-actions">
          <button className="ghost-button" disabled={busy} type="button" onClick={onEnd}>
            <CheckCircle2 size={15} />
            {zh ? "结束迭代，进入问答" : "End iteration and enter Q&A"}
          </button>
          <button className="main-action" disabled={busy} type="button" onClick={onContinue}>
            <RotateCcw className={busy ? "spin" : ""} size={15} />
            {zh ? "根据建议进入下一轮" : "Continue with suggestions"}
          </button>
        </div>
      </div>
    </article>
  );
}

function InlineMarkdown({ text }: { text: string }) {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {tokens.map((token, index) => {
        if (token.startsWith("**") && token.endsWith("**")) {
          return <strong key={`${token}-${index}`}>{token.slice(2, -2)}</strong>;
        }
        if (token.startsWith("`") && token.endsWith("`")) {
          return <code key={`${token}-${index}`}>{token.slice(1, -1)}</code>;
        }
        return <span key={`${token}-${index}`}>{token}</span>;
      })}
    </>
  );
}

function RichMessageBody({ text }: { text: string }) {
  const normalized = text
    .replace(/\s+(?=\d+[.)]\s+\*\*)/g, "\n")
    .replace(/\s+(?=(?:综上|总之|因此)[，,:：])/g, "\n\n");
  const lines = normalized.split(/\r?\n/).map((line) => line.trim());
  const blocks: ReactNode[] = [];
  let lineIndex = 0;

  while (lineIndex < lines.length) {
    const line = lines[lineIndex];
    if (!line) {
      lineIndex += 1;
      continue;
    }

    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      blocks.push(<h4 key={`heading-${lineIndex}`}><InlineMarkdown text={heading[1]} /></h4>);
      lineIndex += 1;
      continue;
    }

    if (/^\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (lineIndex < lines.length && /^\d+[.)]\s+/.test(lines[lineIndex])) {
        items.push(lines[lineIndex].replace(/^\d+[.)]\s+/, ""));
        lineIndex += 1;
      }
      blocks.push(
        <ol key={`ordered-${lineIndex}`}>
          {items.map((item, index) => <li key={`${item}-${index}`}><InlineMarkdown text={item} /></li>)}
        </ol>,
      );
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (lineIndex < lines.length && /^[-*]\s+/.test(lines[lineIndex])) {
        items.push(lines[lineIndex].replace(/^[-*]\s+/, ""));
        lineIndex += 1;
      }
      blocks.push(
        <ul key={`unordered-${lineIndex}`}>
          {items.map((item, index) => <li key={`${item}-${index}`}><InlineMarkdown text={item} /></li>)}
        </ul>,
      );
      continue;
    }

    const paragraph: string[] = [];
    while (
      lineIndex < lines.length
      && lines[lineIndex]
      && !/^#{1,4}\s+/.test(lines[lineIndex])
      && !/^\d+[.)]\s+/.test(lines[lineIndex])
      && !/^[-*]\s+/.test(lines[lineIndex])
    ) {
      paragraph.push(lines[lineIndex]);
      lineIndex += 1;
    }
    blocks.push(
      <p key={`paragraph-${lineIndex}`}><InlineMarkdown text={paragraph.join(" ")} /></p>,
    );
  }

  return <div className="rich-message-body">{blocks}</div>;
}

function ThreadMessageCard({
  feedbackValue,
  language,
  message,
  actionBusy,
  onApprove,
  onFeedbackChange,
  onOpenJson,
  onRerun,
  onSelectStage,
  running,
  t,
}: {
  actionBusy: boolean;
  feedbackValue: string;
  language: Language;
  message: ThreadMessage;
  onApprove: () => void;
  onFeedbackChange: (value: string) => void;
  onOpenJson: (title: string, data: unknown) => void;
  onRerun: () => void;
  onSelectStage: (stage: StageId) => void;
  running: boolean;
  t: (typeof copy)[Language];
}) {
  if (message.kind === "user") {
    return (
      <article className="thread-message user-message" id={message.id}>
        <div className="message-avatar">你</div>
        <div className="message-bubble">
          <header>
            <strong>{message.stage ? t.userRevision : t.userQuestion}</strong>
            <time>{formatTime(message.createdAt)}</time>
          </header>
          {message.body ? <RichMessageBody text={message.body} /> : null}
          {message.attachments?.length ? (
            <div className="message-attachments" aria-label={language === "zh" ? "消息附件" : "Message attachments"}>
              {message.attachments.map((attachment) => (
                <span
                  className={`message-attachment ${attachment.upload_status ?? "completed"}`}
                  key={attachment.attachment_id}
                  title={`${attachment.name} · ${formatBytes(attachment.size)}`}
                >
                  <Paperclip size={13} />
                  <b>{attachment.name}</b>
                  <small>
                    {attachment.upload_status === "pending"
                      ? language === "zh" ? "上传中" : "Uploading"
                      : attachment.upload_status === "failed"
                        ? language === "zh" ? "失败" : "Failed"
                        : attachment.parse_status === "failed"
                          ? language === "zh" ? "解析失败" : "Parse failed"
                        : attachment.parse_status === "pending"
                          ? language === "zh" ? "解析中" : "Parsing"
                          : attachment.chunk_count
                            ? `${formatBytes(attachment.size)} · ${attachment.chunk_count}`
                            : formatBytes(attachment.size)}
                  </small>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </article>
    );
  }

  const title = getMessageTitle(message, language);
  const stage = message.stage;

  return (
    <article className={`thread-message ${message.kind}-message ${message.status ?? ""}`} id={message.id}>
      <div aria-hidden="true" className="message-avatar ai-message-avatar">
        <img
          alt=""
          className="brand-logo-light"
          draggable="false"
          src="/brand/eurekaloop-logo-64.png"
        />
        <img
          alt=""
          className="brand-logo-dark"
          draggable="false"
          src="/brand/eurekaloop-logo-64-dark.png"
        />
      </div>
      <div className="message-bubble">
        <header>
          <div>
            <strong>{title}</strong>
            {stage ? (
              <small>
                {stageMeta[stage].agent} · {language === "zh"
                  ? `第 ${messageIteration(message)} 轮`
                  : `Iteration ${messageIteration(message)}`}
              </small>
            ) : null}
          </div>
          <div className="message-status-group">
            <MessageRuntime
              active={message.status === "running" || message.status === "validating"}
              createdAt={message.createdAt}
              durationMs={message.durationMs ?? message.response?.metadata.duration_ms ?? undefined}
              language={language}
            />
            <span className={`state-chip ${message.status ?? "queued"}`}>{statusLabel[language][message.status ?? "queued"]}</span>
          </div>
        </header>

        {message.body ? <RichMessageBody text={message.body} /> : null}
        {message.activity ? (
          <div className={`agent-live-activity ${message.status ?? "running"}`}>
            <span className="agent-live-icon">
              <Loader2 className={message.status === "running" || message.status === "validating" ? "spin" : ""} size={16} />
            </span>
            <div>
              <div className="agent-live-title">
                <strong>
                  {language === "zh"
                    ? message.status === "validating" ? "正在校验结果" : "当前正在进行"
                    : message.status === "validating" ? "Validating result" : "Current activity"}
                </strong>
                {message.activityNode ? <span>{message.activityNode}</span> : null}
              </div>
              <p>{message.activity}</p>
              <i className={typeof message.activityProgress === "number" ? "" : "indeterminate"}>
                <b style={typeof message.activityProgress === "number"
                  ? { width: `${Math.round(message.activityProgress * 100)}%` }
                  : undefined}
                />
              </i>
            </div>
          </div>
        ) : null}
        {stage && message.revisionNote ? (
          <p className="revision-note">
            {language === "zh" ? "本次重跑依据：" : "Rerun note:"} {message.revisionNote}
          </p>
        ) : null}
        {stage ? (
          <button className="stage-purpose" type="button" onClick={() => onSelectStage(stage)}>
            <Clock3 size={14} />
            {stagePurpose[language][stage]}
          </button>
        ) : null}

        {stage ? <AgentOutput language={language} response={message.response ?? null} stage={stage} /> : null}

        {stage && message.response ? (
          <footer className="message-footer">
            <div className="score-row">
              <span>Self {Math.round(message.response.self_review.overall_score * 100)}%</span>
              <span>{stageMeta[stage].allowedWrites.join(", ")}</span>
            </div>
            <button
              className="text-button"
              type="button"
              onClick={() =>
                onOpenJson(language === "zh" ? `${stageLabel.zh[stage]} JSON` : `${stageLabel.en[stage]} JSON`, {
                  stage,
                  output: message.response,
                  review: message.review,
                })
              }
            >
              <FileJson size={15} />
              {t.json}
            </button>
          </footer>
        ) : null}

        {message.needsApproval && stage ? (
          <section className="inline-review">
            <p>{t.gateWaiting}</p>
            <div className="review-actions">
              <button className="main-action" disabled={running || actionBusy} type="button" onClick={onApprove}>
                <CheckCircle2 size={16} />
                {t.approveContinue}
              </button>
            </div>
            <div className="revision-composer">
              <textarea
                disabled={running || actionBusy}
                onChange={(event) => onFeedbackChange(event.target.value)}
                placeholder={t.revisePlaceholder}
                value={feedbackValue}
              />
              <button className="ghost-button" disabled={running || actionBusy} type="button" onClick={onRerun}>
                {t.revise}
              </button>
            </div>
          </section>
        ) : stage && message.status === "revision_required" ? (
          <section className="inline-review revision-required-actions">
            <p>
              {language === "zh"
                ? "当前输出低于建议质量阈值，但结构和追溯校验结果仍可查看。你可以保留当前结果继续，也可以让系统重新执行。"
                : "The result is below the recommended quality threshold, but its structure and traceability checks remain available. Continue with it or let the system rerun."}
            </p>
            <BulletList
              label={language === "zh" ? "审查问题" : "Review issues"}
              values={message.response?.self_review.issues ?? []}
            />
            <BulletList
              label={language === "zh" ? "补证建议" : "Evidence suggestions"}
              values={message.response?.self_review.suggestions ?? []}
            />
            <div className="system-revision-action">
              <span>{language === "zh" ? "重新执行时，修订意见由总控自动生成，无需填写。" : "For a rerun, the controller generates revision guidance automatically."}</span>
              <div className="quality-choice-buttons">
                <button className="main-action" disabled={running || actionBusy} type="button" onClick={onApprove}>
                  <CheckCircle2 size={16} />
                  {language === "zh" ? "继续执行" : "Continue"}
                </button>
                <button className="ghost-button" disabled={running || actionBusy} type="button" onClick={onRerun}>
                  <RotateCcw size={15} />
                  {language === "zh" ? "重新执行" : "Rerun"}
                </button>
              </div>
            </div>
          </section>
        ) : stage && message.status === "failed" ? (
          <section className="inline-review agent-failure-actions">
            <div className="revision-composer">
              <textarea
                disabled={running}
                onChange={(event) => onFeedbackChange(event.target.value)}
                placeholder={language === "zh" ? "补充配置或修改意见后重试" : "Fix configuration or add feedback, then retry"}
                value={feedbackValue}
              />
              <button className="ghost-button" disabled={running} type="button" onClick={onRerun}>
                <RotateCcw size={15} />
                {language === "zh" ? "重试" : "Retry"}
              </button>
            </div>
          </section>
        ) : stage && message.status === "passed" ? (
          <p className="gate-note">{t.gatePassed}</p>
        ) : stage && message.status === "retrying" ? (
          <p className="gate-note">{t.retryQueued}</p>
        ) : null}
      </div>
    </article>
  );
}

function KnowledgeIntegrationOutput({
  language,
  payload,
}: {
  language: Language;
  payload: Record<string, unknown>;
}) {
  const literature = arrayValue(payload.literature_cards);
  const evidence = arrayValue(payload.evidence_cards);
  const gaps = arrayValue(payload.knowledge_gaps);
  const phases = [
    {
      id: "literature",
      icon: FileJson,
      title: language === "zh" ? "文献检索" : "Literature search",
      subtitle: language === "zh" ? "形成可追踪的文献卡片" : "Build traceable literature cards",
      count: literature.length,
      empty: language === "zh" ? "尚未返回文献卡片。" : "No literature cards returned.",
    },
    {
      id: "evidence",
      icon: CheckCircle2,
      title: language === "zh" ? "证据整合" : "Evidence integration",
      subtitle: language === "zh" ? "抽取可被下游引用的证据结论" : "Extract evidence claims for downstream use",
      count: evidence.length,
      empty: language === "zh" ? "尚未返回证据卡片。" : "No evidence cards returned.",
    },
    {
      id: "gaps",
      icon: HelpCircle,
      title: language === "zh" ? "知识空白合成" : "Knowledge gap synthesis",
      subtitle: language === "zh" ? "确定假设生成必须引用的 gap_id" : "Identify gap_id references required by hypotheses",
      count: gaps.length,
      empty: language === "zh" ? "尚未返回知识空白，假设生成会被后端门禁拦截。" : "No knowledge gaps returned; hypothesis generation will be blocked.",
    },
  ];

  return (
    <div className="module-output knowledge-output">
      <div className="knowledge-phase-strip">
        {phases.map((phase, index) => {
          const Icon = phase.icon;
          return (
            <div className={`knowledge-phase ${phase.count ? "ready" : "empty"}`} key={phase.id}>
              <i>{index + 1}</i>
              <Icon size={15} />
              <div>
                <strong>{phase.title}</strong>
                <span>{phase.subtitle}</span>
              </div>
              <b>{phase.count}</b>
            </div>
          );
        })}
      </div>

      <section className="knowledge-section">
        <header>
          <span>{language === "zh" ? "文献卡片" : "Literature cards"}</span>
          <small>{literature.length}</small>
        </header>
        {literature.length ? (
          <div className="knowledge-card-grid">
            {literature.map((item, index) => (
              <article id={`artifact-${objectField(item, "literature_id")}`} key={`${objectField(item, "literature_id")}-${index}`}>
                <div>
                  <strong>{objectField(item, "title") || objectField(item, "literature_id")}</strong>
                  <small>{[objectField(item, "year"), objectField(item, "source")].filter(Boolean).join(" · ") || "--"}</small>
                </div>
                <p>{trimPreview(objectField(item, "abstract") || objectField(item, "summary"), 150)}</p>
                <code>{objectField(item, "doi") || objectField(item, "url") || objectField(item, "literature_id")}</code>
              </article>
            ))}
          </div>
        ) : (
          <p className="knowledge-empty">{phases[0].empty}</p>
        )}
      </section>

      <section className="knowledge-section">
        <header>
          <span>{language === "zh" ? "证据卡片" : "Evidence cards"}</span>
          <small>{evidence.length}</small>
        </header>
        {evidence.length ? (
          <div className="knowledge-card-grid">
            {evidence.map((item, index) => (
              <article id={`artifact-${objectField(item, "evidence_id")}`} key={`${objectField(item, "evidence_id")}-${index}`}>
                <div>
                  <strong>{objectField(item, "evidence_id")}</strong>
                  <small>{language === "zh" ? "来源" : "Source"}: {objectField(item, "source_literature_id") || "--"}</small>
                </div>
                <p>{objectField(item, "claim") || "--"}</p>
                <code>{objectField(item, "evidence_type") || objectField(item, "strength") || "--"}</code>
              </article>
            ))}
          </div>
        ) : (
          <p className="knowledge-empty">{phases[1].empty}</p>
        )}
      </section>

      <section className="knowledge-section">
        <header>
          <span>{language === "zh" ? "知识空白" : "Knowledge gaps"}</span>
          <small>{gaps.length}</small>
        </header>
        {gaps.length ? (
          <div className="knowledge-gap-list">
            {gaps.map((item, index) => (
              <article id={`artifact-${objectField(item, "gap_id")}`} key={`${objectField(item, "gap_id")}-${index}`}>
                <code>{objectField(item, "gap_id") || `gap_${index + 1}`}</code>
                <p>{objectField(item, "description") || "--"}</p>
                <small>{objectField(item, "research_opportunity") || objectField(item, "why_it_matters")}</small>
              </article>
            ))}
          </div>
        ) : (
          <p className="knowledge-empty warning">{phases[2].empty}</p>
        )}
      </section>
    </div>
  );
}

function AgentOutput({ language, response, stage }: { language: Language; response: AgentResponse | null; stage: StageId }) {
  if (!response) {
    return (
      <div className="output-skeleton">
        <Loader2 className="spin" size={16} />
        <span>{language === "zh" ? "正在生成模块输出..." : "Generating module output..."}</span>
      </div>
    );
  }

  if (response.metadata.status === "failed") {
    return (
      <div className="module-output agent-error-output">
        <BulletList
          label={language === "zh" ? "错误详情" : "Error details"}
          values={response.self_review.issues}
        />
        <BulletList
          label={language === "zh" ? "处理建议" : "Suggested actions"}
          values={response.self_review.suggestions}
        />
      </div>
    );
  }

  const payload = response.payload as Record<string, unknown>;

  if (stage === "question_understanding") {
    const card = payload.question_card as Record<string, unknown>;
    return (
      <div className="module-output">
        <KeyValue label={language === "zh" ? "核心问题" : "Core question"} value={stringValue(card.core_question)} />
        <PillList label={language === "zh" ? "研究领域" : "Domains"} values={arrayValue(card.domain)} />
        <PillList label={language === "zh" ? "关键变量" : "Key variables"} values={arrayValue(card.key_variables).map((item) => objectName(item))} />
        <BulletList label={language === "zh" ? "拆解子问题" : "Sub-questions"} values={arrayValue(card.sub_questions).map((item) => objectField(item, "content"))} />
      </div>
    );
  }

  if (stage === "knowledge_integration") {
    return <KnowledgeIntegrationOutput language={language} payload={payload} />;
  }

  if (stage === "hypothesis_generation") {
    return (
      <div className="module-output">
        {arrayValue(payload.hypothesis_cards).map((item, index) => (
          <article className="hypothesis-card" key={`${objectField(item, "hypothesis_id")}-${index}`}>
            <strong>{objectField(item, "hypothesis_id")}</strong>
            <p>{objectField(item, "statement")}</p>
            <small>{objectField(item, "validation_idea")}</small>
          </article>
        ))}
      </div>
    );
  }

  if (stage === "evidence_mapping") {
    return (
      <div className="module-output">
        {arrayValue(payload.evidence_map).map((item, index) => (
          <article className="evidence-card" key={`${objectField(item, "hypothesis_id")}-${index}`}>
            <strong>{objectField(item, "hypothesis_id")}</strong>
            <p>{objectField(item, "evidence_summary.support")}</p>
            <div className="evidence-grid">
              <span>{language === "zh" ? "支持" : "Support"} {arrayValue(objectValue(item, "supporting_evidence_ids")).join(", ")}</span>
              <span>{language === "zh" ? "反对" : "Oppose"} {arrayValue(objectValue(item, "opposing_evidence_ids")).join(", ") || "--"}</span>
              <span>{language === "zh" ? "强度" : "Strength"} {Math.round(numberValue(objectValue(item, "evidence_strength_score")) * 100)}%</span>
            </div>
          </article>
        ))}
      </div>
    );
  }

  if (stage === "research_planning") {
    return <ResearchPlanOutput language={language} researchPlan={payload.research_plan as ResearchPlan} />;
  }

  const finalReview = payload.final_review as Record<string, unknown>;
  return (
    <div className="module-output final-output">
      <KeyValue label={language === "zh" ? "总评分" : "Overall score"} value={`${Math.round(numberValue(finalReview.overall_score) * 100)}%`} />
      <BulletList label={language === "zh" ? "优势" : "Strengths"} values={arrayValue(finalReview.strengths)} />
      <BulletList label={language === "zh" ? "不足" : "Weaknesses"} values={arrayValue(finalReview.weaknesses)} />
      <KeyValue label={language === "zh" ? "是否需要修订" : "Revision required"} value={finalReview.revision_required ? "Yes" : language === "zh" ? "否" : "No"} />
    </div>
  );
}

type ResearchPlanItem = ResearchPlan["plans"][number];

function normalizeResearchPlan(plan: ResearchPlanItem["plan"] | null | undefined): ResearchPlanItem["plan"] {
  const methods = plan?.methods;
  const datasets = plan?.datasets;
  const experiments = plan?.experiments;
  const mainExperiment = experiments?.main_experiment;
  const results = plan?.results;
  const rationale = plan?.rationale;
  const technical = plan?.technical_details;
  return {
    ...plan,
    problem_statement: plan?.problem_statement ?? "",
    paper_title: plan?.paper_title ?? "",
    paper_abstract: plan?.paper_abstract ?? "",
    technical_details: {
      ...technical,
      required_methods: technical?.required_methods ?? [],
      candidate_models_or_algorithms: technical?.candidate_models_or_algorithms ?? [],
      statistical_tests: technical?.statistical_tests ?? [],
      software_stack: technical?.software_stack ?? [],
    },
    methods: {
      ...methods,
      overall_design: methods?.overall_design ?? "",
      steps: (methods?.steps ?? []).map((step) => ({
        ...step,
        input: step.input ?? [],
        output: step.output ?? [],
      })),
    },
    datasets: {
      ...datasets,
      source: (datasets?.source ?? []).map((dataset) => ({
        ...dataset,
        required_fields: dataset.required_fields ?? [],
      })),
      target: (datasets?.target ?? []).map((dataset) => ({
        ...dataset,
        fields: dataset.fields ?? [],
      })),
    },
    experiments: {
      ...experiments,
      main_experiment: {
        ...mainExperiment,
        independent_variables: mainExperiment?.independent_variables ?? [],
        dependent_variables: mainExperiment?.dependent_variables ?? [],
        control_variables: mainExperiment?.control_variables ?? [],
      },
      metrics: experiments?.metrics ?? [],
      baselines: experiments?.baselines ?? [],
      procedure: experiments?.procedure ?? [],
      ablation_or_sensitivity_analysis: experiments?.ablation_or_sensitivity_analysis ?? [],
    },
    results: {
      ...results,
      result_type: results?.result_type ?? "",
      expected_findings: results?.expected_findings ?? [],
      feasibility_check: results?.feasibility_check ?? "",
      falsification_criteria: results?.falsification_criteria ?? [],
    },
    rationale: {
      ...rationale,
      text: rationale?.text ?? "",
      logic_chain: (rationale?.logic_chain ?? []).map((step) => ({
        ...step,
        evidence_ids: step.evidence_ids ?? [],
        source_ids: step.source_ids ?? [],
      })),
    },
    references: (plan?.references ?? []).map((reference) => ({
      ...reference,
      authors: reference.authors ?? [],
      used_for: reference.used_for ?? [],
    })),
    feedback_tasks: (plan?.feedback_tasks ?? []).map((task) => ({
      ...task,
      input_requirements: task.input_requirements ?? [],
    })),
    limitations: plan?.limitations ?? [],
  } as ResearchPlanItem["plan"];
}

function planMarkdown(item: ResearchPlanItem) {
  const plan = normalizeResearchPlan(item.plan);
  const section = (title: string, values: string[]) => `\n## ${title}\n${values.length ? values.map((value) => `- ${value}`).join("\n") : "- --"}\n`;
  return [
    `# ${plan.paper_title || item.hypothesis_id}`,
    `\n**Hypothesis:** ${item.hypothesis_id}`,
    `\n**Status:** ${item.status}`,
    `\n## Problem\n${plan.problem_statement}`,
    `\n## Overall Design\n${plan.methods.overall_design}`,
    section("Methods", plan.methods.steps.map((step) => `${step.name}: ${step.description}`)),
    section("Datasets", plan.datasets.source.map((dataset) => `${dataset.name}: ${dataset.usage} (${dataset.access_status})`)),
    section("Metrics", plan.experiments.metrics.map((metric) => `${metric.name}: ${metric.description}`)),
    section("Procedure", plan.experiments.procedure),
    section("Expected Findings", plan.results.expected_findings),
    section("Falsification Criteria", plan.results.falsification_criteria),
    section("References", plan.references.map((reference) => `${reference.title} (${reference.year}) ${reference.doi || reference.url}`)),
    section("Limitations", plan.limitations),
  ].join("\n");
}

function ResearchPlanOutput({ language, researchPlan }: { language: Language; researchPlan?: ResearchPlan }) {
  const plans = researchPlan?.plans ?? [];
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [copied, setCopied] = useState(false);
  const selected = plans[Math.min(selectedIndex, Math.max(0, plans.length - 1))];

  if (!selected) {
    return <div className="module-output"><p>{language === "zh" ? "研究计划未返回可展示内容。" : "No research plan was returned."}</p></div>;
  }

  const plan = normalizeResearchPlan(selected.plan);
  const copyPlan = async () => {
    await navigator.clipboard.writeText(planMarkdown(selected));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  const downloadPlan = () => {
    const blob = new Blob([planMarkdown(selected)], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selected.hypothesis_id || "research-plan"}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="module-output research-plan-output">
      <div className="plan-toolbar">
        <div className="plan-tabs" role="tablist" aria-label={language === "zh" ? "研究计划" : "Research plans"}>
          {plans.map((item, index) => (
            <button
              aria-selected={index === selectedIndex}
              className={index === selectedIndex ? "active" : ""}
              key={`${item.hypothesis_id}-${index}`}
              role="tab"
              type="button"
              onClick={() => setSelectedIndex(index)}
            >
              {item.hypothesis_id}
              <i className={item.status} />
            </button>
          ))}
        </div>
        <div className="plan-actions">
          <button type="button" onClick={() => void copyPlan()}><Copy size={13} />{copied ? (language === "zh" ? "已复制" : "Copied") : (language === "zh" ? "复制" : "Copy")}</button>
          <button type="button" onClick={downloadPlan}><Download size={13} />Markdown</button>
        </div>
      </div>

      {selected.status === "failed" ? (
        <div className="plan-error"><strong>{language === "zh" ? "该假设规划失败" : "Planning failed"}</strong><p>{selected.error_message || "--"}</p></div>
      ) : null}

      <KeyValue label={language === "zh" ? "研究问题" : "Problem"} value={plan.problem_statement} />
      <KeyValue label={language === "zh" ? "论文题目" : "Paper title"} value={plan.paper_title} />
      <KeyValue label={language === "zh" ? "摘要" : "Abstract"} value={plan.paper_abstract} />
      <KeyValue label={language === "zh" ? "科学依据" : "Rationale"} value={plan.rationale.text} />

      <section className="plan-section">
        <h4>{language === "zh" ? "研究设计" : "Research design"}</h4>
        <p>{plan.methods.overall_design}</p>
        <div className="plan-timeline">
          {plan.methods.steps.map((step, index) => (
            <article key={`${step.step_id}-${index}`}>
              <b>{String(index + 1).padStart(2, "0")}</b>
              <div><strong>{step.name}</strong><p>{step.description}</p><small>{step.input.join(", ")} → {step.output.join(", ")}</small></div>
            </article>
          ))}
        </div>
      </section>

      <section className="plan-section plan-grid">
        <div>
          <h4>{language === "zh" ? "技术路线" : "Technical route"}</h4>
          <BulletList label={language === "zh" ? "方法" : "Methods"} values={plan.technical_details.required_methods} />
          <BulletList label={language === "zh" ? "模型/算法" : "Models/algorithms"} values={plan.technical_details.candidate_models_or_algorithms} />
        </div>
        <div>
          <h4>{language === "zh" ? "统计与软件" : "Statistics and software"}</h4>
          <BulletList label={language === "zh" ? "统计检验" : "Statistical tests"} values={plan.technical_details.statistical_tests} />
          <BulletList label={language === "zh" ? "软件栈" : "Software stack"} values={plan.technical_details.software_stack} />
        </div>
      </section>

      <section className="plan-section plan-grid">
        <div><h4>{language === "zh" ? "实验变量" : "Variables"}</h4><PillList label={language === "zh" ? "自变量" : "Independent"} values={plan.experiments.main_experiment.independent_variables} /><PillList label={language === "zh" ? "因变量" : "Dependent"} values={plan.experiments.main_experiment.dependent_variables} /><PillList label={language === "zh" ? "控制变量" : "Controls"} values={plan.experiments.main_experiment.control_variables} /></div>
        <div><h4>{language === "zh" ? "指标与基线" : "Metrics and baselines"}</h4><BulletList label={language === "zh" ? "指标" : "Metrics"} values={plan.experiments.metrics.map((item) => `${item.name}: ${item.description}`)} /><BulletList label={language === "zh" ? "基线" : "Baselines"} values={plan.experiments.baselines.map((item) => `${item.name}: ${item.description}`)} /></div>
      </section>

      <section className="plan-section">
        <h4>{language === "zh" ? "数据" : "Data"}</h4>
        <div className="dataset-grid">
          {plan.datasets.source.map((dataset, index) => (
            <article key={`${dataset.dataset_id}-${index}`}><strong>{dataset.name}</strong><p>{dataset.usage}</p><small>{dataset.access_status} · {dataset.required_fields.join(", ")}</small></article>
          ))}
          {plan.datasets.target.map((dataset, index) => (
            <article key={`${dataset.name}-${index}`}><strong>{dataset.name}</strong><p>{dataset.description}</p><small>{dataset.fields.join(", ")}</small></article>
          ))}
        </div>
      </section>

      <section className="plan-section">
        <h4>{language === "zh" ? "实验流程" : "Experiment procedure"}</h4>
        <div className="plan-procedure">
          {plan.experiments.procedure.length ? plan.experiments.procedure.map((step, index) => (
            <article key={`${step}-${index}`}><b>{String(index + 1).padStart(2, "0")}</b><p>{step}</p></article>
          )) : <article><b>--</b><p>--</p></article>}
        </div>
      </section>

      <section className="plan-section plan-grid">
        <div><h4>{language === "zh" ? "预期结果" : "Expected results"}</h4><BulletList label={plan.results.result_type} values={plan.results.expected_findings} /><KeyValue label={language === "zh" ? "可行性" : "Feasibility"} value={plan.results.feasibility_check} /></div>
        <div><h4>{language === "zh" ? "证伪与敏感性" : "Falsification and sensitivity"}</h4><BulletList label={language === "zh" ? "失败判据" : "Falsification"} values={plan.results.falsification_criteria} /><BulletList label={language === "zh" ? "消融/敏感性" : "Ablation/sensitivity"} values={plan.experiments.ablation_or_sensitivity_analysis} /></div>
      </section>

      <section className="plan-section">
        <h4>{language === "zh" ? "证据与参考文献" : "Evidence and references"}</h4>
        <div className="trace-links">
          {plan.rationale.logic_chain.flatMap((step) => step.evidence_ids).filter((id, index, values) => values.indexOf(id) === index).map((id) => <a href={`#artifact-${id}`} key={id}>{id}</a>)}
        </div>
        <div className="logic-chain-list">
          {plan.rationale.logic_chain.length ? plan.rationale.logic_chain.map((step, index) => (
            <article key={`${step.step}-${index}`}>
              <b>{step.step ?? index + 1}</b>
              <div>
                <strong>{step.claim}</strong>
                <small>{[...step.evidence_ids, ...step.source_ids].join(" · ") || "--"}</small>
              </div>
            </article>
          )) : null}
        </div>
        <div className="reference-list">
          {plan.references.map((reference, index) => (
            <article key={`${reference.source_id}-${index}`}><strong>{reference.title}</strong><p>{reference.authors.join(", ")} · {reference.year}</p><small>{reference.used_for.join(", ")}</small><a href={reference.url || (reference.doi ? `https://doi.org/${reference.doi}` : undefined)} target="_blank" rel="noreferrer">{reference.doi || reference.source_id}</a></article>
          ))}
        </div>
      </section>

      <section className="plan-section plan-grid">
        <BulletList label={language === "zh" ? "限制" : "Limitations"} values={plan.limitations} />
        <div>
          <h4>{language === "zh" ? "反馈任务" : "Feedback tasks"}</h4>
          <div className="feedback-task-list">
            {plan.feedback_tasks.length ? plan.feedback_tasks.map((item, index) => (
              <article key={`${item.task_id}-${index}`}>
                <strong>{item.task_id || item.task_type}</strong>
                <p>[{item.priority}] {item.objective}</p>
                <small>{item.input_requirements.join(", ") || "--"} → {item.expected_output || "--"}</small>
              </article>
            )) : <article><strong>--</strong><p>--</p></article>}
          </div>
        </div>
      </section>

      <details className="json-fallback-panel">
        <summary>{language === "zh" ? "完整 JSON 兜底" : "Complete JSON fallback"}</summary>
        <JsonTree data={selected.plan ?? selected} rootLabel={language === "zh" ? "研究计划" : "research_plan"} />
      </details>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="key-value">
      <span>{label}</span>
      <p>{value || "--"}</p>
    </div>
  );
}

function PillList({ label, values }: { label: string; values: unknown[] }) {
  return (
    <div className="output-section">
      <span>{label}</span>
      <div className="pill-list">
        {values.length ? values.map((value, index) => <b key={`${String(value)}-${index}`}>{String(value)}</b>) : <b>--</b>}
      </div>
    </div>
  );
}

function BulletList({ label, values }: { label: string; values: unknown[] }) {
  return (
    <div className="output-section">
      <span>{label}</span>
      <ul>
        {values.length ? values.map((value, index) => <li key={`${String(value)}-${index}`}>{String(value)}</li>) : <li>--</li>}
      </ul>
    </div>
  );
}

function JsonTree({
  data,
  depth = 0,
  rootLabel = "root",
}: {
  data: unknown;
  depth?: number;
  rootLabel?: string;
}) {
  if (Array.isArray(data)) {
    return (
      <details className="json-tree-node" open={depth < 2}>
        <summary><code>{rootLabel}</code><span>Array[{data.length}]</span></summary>
        <div className="json-tree-children">
          {data.length ? data.map((item, index) => (
            <JsonTree data={item} depth={depth + 1} key={`${rootLabel}-${index}`} rootLabel={String(index)} />
          )) : <span className="json-tree-empty">[]</span>}
        </div>
      </details>
    );
  }

  if (data && typeof data === "object") {
    const entries = Object.entries(data as Record<string, unknown>);
    return (
      <details className="json-tree-node" open={depth < 2}>
        <summary><code>{rootLabel}</code><span>Object{`{${entries.length}}`}</span></summary>
        <div className="json-tree-children">
          {entries.length ? entries.map(([key, value]) => (
            <JsonTree data={value} depth={depth + 1} key={key} rootLabel={key} />
          )) : <span className="json-tree-empty">{`{}`}</span>}
        </div>
      </details>
    );
  }

  return (
    <div className="json-tree-leaf">
      <code>{rootLabel}</code>
      <span>{data === null ? "null" : JSON.stringify(data) ?? String(data)}</span>
    </div>
  );
}

function StatusMetric({ label, title, value }: { label: string; title?: string; value: string }) {
  return (
    <div className="status-metric" title={title}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function trimPreview(value: unknown, maxLength = 92) {
  const text = stringValue(value).replace(/\s+/g, " ").trim();
  if (!text) return "--";
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

function joinPreview(values: unknown[], mapper: (value: unknown) => string, language: Language, maxItems = 2) {
  const separator = language === "zh" ? "；" : "; ";
  const items = values.map(mapper).map((value) => value.trim()).filter(Boolean).slice(0, maxItems);
  return items.length ? trimPreview(items.join(separator), 118) : "--";
}

function percentPreview(value: unknown) {
  const score = numberValue(value);
  return score > 0 ? `${Math.round(score * 100)}%` : "--";
}

function getStageArtifactSummary(stage: Pick<StageRun, "output">, field: string, language: Language) {
  const payload = (stage.output?.payload ?? {}) as Record<string, unknown>;
  if (!stage.output) return language === "zh" ? "等待该模块输出后写入 task_context" : "Waiting for this module output";
  if (stage.output.metadata.status === "failed") {
    const reason = stage.output.self_review.issues[0]
      ?? (language === "zh" ? "模块执行失败，未写入本阶段产物。" : "The module failed and did not write this artifact.");
    return trimPreview(language === "zh" ? `本轮未写入：${reason}` : `Not written in this iteration: ${reason}`, 118);
  }

  if (field === "question_card") {
    const card = payload.question_card as Record<string, unknown> | undefined;
    return trimPreview(`${language === "zh" ? "核心问题" : "Core"}：${stringValue(card?.core_question)}`, 118);
  }
  if (field === "literature_cards") {
    return joinPreview(
      arrayValue(payload.literature_cards),
      (item) => `${objectField(item, "title")} (${objectField(item, "year")})`,
      language,
    );
  }
  if (field === "evidence_cards") {
    return joinPreview(arrayValue(payload.evidence_cards), (item) => objectField(item, "claim"), language);
  }
  if (field === "knowledge_gaps") {
    return joinPreview(arrayValue(payload.knowledge_gaps), (item) => objectField(item, "description"), language);
  }
  if (field === "hypothesis_cards") {
    return joinPreview(
      arrayValue(payload.hypothesis_cards),
      (item) => `${objectField(item, "hypothesis_id")}：${objectField(item, "statement")}`,
      language,
    );
  }
  if (field === "evidence_map") {
    return joinPreview(
      arrayValue(payload.evidence_map),
      (item) => `${objectField(item, "hypothesis_id")} ${language === "zh" ? "强度" : "strength"} ${percentPreview(objectValue(item, "evidence_strength_score"))}`,
      language,
      3,
    );
  }
  if (field === "reviews") {
    const review = objectValue(arrayValue(payload.evidence_map)[0], "detailed_review.verdict");
    return trimPreview(`${language === "zh" ? "建议" : "Recommendation"}：${objectField(review, "recommendation")}`, 118);
  }
  if (field === "research_plan") {
    const plan = ((payload.research_plan as Record<string, unknown> | undefined)?.plans as Array<Record<string, unknown>> | undefined)?.[0]?.plan as Record<string, unknown> | undefined;
    return trimPreview(`${language === "zh" ? "方案" : "Plan"}：${stringValue(plan?.problem_statement)}`, 118);
  }
  if (field === "final_review") {
    const review = payload.final_review as Record<string, unknown> | undefined;
    return trimPreview(`${language === "zh" ? "总评分" : "Score"} ${percentPreview(review?.overall_score)} · ${arrayValue(review?.strengths).slice(0, 1).join("")}`, 118);
  }
  if (field === "versions") {
    const review = payload.final_review as Record<string, unknown> | undefined;
    return review?.revision_required ? (language === "zh" ? "需要生成修订版本" : "Revision version required") : language === "zh" ? "可生成最终上下文快照" : "Ready for final context snapshot";
  }
  return language === "zh" ? "已生成，等待总控合并" : "Generated and ready for merge";
}

function getStageExpansionNodes(stage: StageRun, language: Language): StateTreeDetailNode[] {
  const payload = (stage.output?.payload ?? {}) as Record<string, unknown>;
  if (stage.id === "question_understanding") {
    const card = payload.question_card as Record<string, unknown> | undefined;
    return [
      {
        id: "core_question",
        sourceArtifact: "question_card",
        title: language === "zh" ? "核心问题" : "Core question",
        subtitle: trimPreview(card?.core_question, 120),
      },
      {
        id: "variables",
        sourceArtifact: "question_card",
        title: language === "zh" ? "变量链路" : "Variable chain",
        subtitle: joinPreview(arrayValue(card?.key_variables), (item) => objectName(item), language, 3),
      },
      {
        id: "sub_questions",
        sourceArtifact: "question_card",
        title: language === "zh" ? "拆解子问题" : "Sub-questions",
        subtitle: joinPreview(arrayValue(card?.sub_questions), (item) => objectField(item, "content"), language, 2),
      },
    ];
  }
  if (stage.id === "knowledge_integration") {
    return [
      {
        id: "literature_preview",
        sourceArtifact: "literature_cards",
        title: language === "zh" ? "代表文献" : "Key literature",
        subtitle: joinPreview(arrayValue(payload.literature_cards), (item) => `${objectField(item, "title")} · ${objectField(item, "source")}`, language),
      },
      {
        id: "evidence_claims",
        sourceArtifact: "evidence_cards",
        title: language === "zh" ? "证据结论" : "Evidence claims",
        subtitle: joinPreview(arrayValue(payload.evidence_cards), (item) => objectField(item, "claim"), language),
      },
      {
        id: "knowledge_gaps",
        sourceArtifact: "knowledge_gaps",
        title: language === "zh" ? "知识空白" : "Knowledge gaps",
        subtitle: joinPreview(arrayValue(payload.knowledge_gaps), (item) => objectField(item, "description"), language),
      },
    ];
  }
  if (stage.id === "hypothesis_generation") {
    return arrayValue(payload.hypothesis_cards)
      .slice(0, 3)
      .map((item) => ({
        id: objectField(item, "hypothesis_id") || "hypothesis",
        sourceArtifact: "hypothesis_cards",
        title: `${objectField(item, "hypothesis_id")} · ${language === "zh" ? "可检验性" : "testability"} ${percentPreview(objectValue(item, "initial_scores.testability"))}`,
        subtitle: trimPreview(`${objectField(item, "statement")} ${language === "zh" ? "验证：" : "Test: "}${objectField(item, "validation_idea")}`, 126),
      }));
  }
  if (stage.id === "evidence_mapping") {
    const maps = arrayValue(payload.evidence_map);
    const mappedNodes = maps.slice(0, 2).map((item) => ({
      id: objectField(item, "hypothesis_id") || "evidence",
      sourceArtifact: "evidence_map",
      title: `${objectField(item, "hypothesis_id")} · ${language === "zh" ? "证据强度" : "strength"} ${percentPreview(objectValue(item, "evidence_strength_score"))}`,
      subtitle: trimPreview(
        `${language === "zh" ? "支持" : "Support"}：${objectField(item, "evidence_summary.support")} ${language === "zh" ? "反对" : "Oppose"}：${objectField(item, "evidence_summary.oppose")}`,
        128,
      ),
    }));
    const firstVerdict = objectValue(maps[0], "detailed_review.verdict");
    return [
      ...mappedNodes,
      {
        id: "review_recommendation",
        sourceArtifact: "reviews",
        title: language === "zh" ? "评审建议" : "Review advice",
        subtitle: trimPreview(`${objectField(firstVerdict, "reason")} ${objectField(firstVerdict, "recommendation")}`, 128),
      },
    ];
  }
  if (stage.id === "research_planning") {
    const plan = ((payload.research_plan as Record<string, unknown> | undefined)?.plans as Array<Record<string, unknown>> | undefined)?.[0]?.plan as Record<string, unknown> | undefined;
    return [
      {
        id: "problem_methods",
        sourceArtifact: "research_plan",
        title: language === "zh" ? "问题与方法" : "Problem and methods",
        subtitle: trimPreview(`${stringValue(plan?.problem_statement)} · ${arrayValue(objectValue(plan?.technical_details, "required_methods")).slice(0, 3).join(", ")}`, 128),
      },
      {
        id: "data_metrics",
        sourceArtifact: "research_plan",
        title: language === "zh" ? "数据与指标" : "Data and metrics",
        subtitle: trimPreview(
          `${joinPreview(arrayValue(objectValue(plan?.datasets, "source")), (item) => objectField(item, "name"), language)} · ${joinPreview(arrayValue(objectValue(plan?.experiments, "metrics")), (item) => objectField(item, "name"), language, 3)}`,
          128,
        ),
      },
      {
        id: "falsification_feedback",
        sourceArtifact: "research_plan",
        title: language === "zh" ? "失败判据与反馈" : "Falsification and feedback",
        subtitle: trimPreview(
          `${joinPreview(arrayValue(objectValue(plan?.results, "falsification_criteria")), (item) => stringValue(item), language)} · ${joinPreview(arrayValue(plan?.feedback_tasks), (item) => objectField(item, "objective"), language)}`,
          128,
        ),
      },
    ];
  }
  const finalReview = payload.final_review as Record<string, unknown> | undefined;
  return [
    {
      id: "overall_score",
      sourceArtifact: "final_review",
      title: language === "zh" ? "总体评分" : "Overall score",
      subtitle: `${percentPreview(finalReview?.overall_score)} · ${joinPreview(arrayValue(finalReview?.strengths), (item) => stringValue(item), language)}`,
    },
    {
      id: "weaknesses",
      sourceArtifact: "final_review",
      title: language === "zh" ? "剩余风险" : "Remaining risks",
      subtitle: joinPreview(arrayValue(finalReview?.weaknesses), (item) => stringValue(item), language),
    },
    {
      id: "delivery_snapshot",
      sourceArtifact: "versions",
      title: language === "zh" ? "交付状态" : "Delivery state",
      subtitle: finalReview?.revision_required ? (language === "zh" ? "需要修订后再形成最终版本。" : "Revision required before final delivery.") : language === "zh" ? "可形成最终 task_context 快照与报告导出。" : "Ready for final task_context snapshot and report export.",
    },
  ];
}

function StateTreeModal({
  activeStage,
  context,
  iteration,
  language,
  onClose,
  onNodeAction,
  onSelectStage,
  stages,
  t,
}: {
  activeStage: StageId;
  context: TaskContext;
  iteration: number;
  language: Language;
  onClose: () => void;
  onNodeAction: (stage: StageId, action: "only" | "from" | "pause" | "history") => void;
  onSelectStage: (stage: StageId) => void;
  stages: StageRun[];
  t: (typeof copy)[Language];
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string>(activeStage);
  const treeLayout = useMemo(
    () => {
      const nextNodes: FlowNode[] = [];
      const lanes: StateTreeLane[] = [];
      const layout = {
        artifactHeight: 82,
        artifactX: 302,
        detailHeight: 88,
        detailX: 558,
        laneGap: 16,
        minLaneHeight: 148,
        rowGap: 100,
        stageHeight: 96,
        stageX: 42,
      };
      let cursorY = 24;
      const getRowY = (laneTop: number, laneHeight: number, rowIndex: number, totalRows: number, nodeHeight: number) => {
        const blockHeight = Math.max(0, totalRows - 1) * layout.rowGap;
        return laneTop + laneHeight / 2 - blockHeight / 2 + rowIndex * layout.rowGap - nodeHeight / 2;
      };

      stages.forEach((stage, stageIndex) => {
        const artifacts = stageMeta[stage.id].allowedWrites;
        const details = stage.output && stage.output.metadata.status !== "failed"
          ? getStageExpansionNodes(stage, language)
          : [];
        const rowCount = Math.max(1, artifacts.length, details.length);
        const laneHeight = Math.max(
          layout.minLaneHeight,
          (rowCount - 1) * layout.rowGap + Math.max(layout.artifactHeight, layout.detailHeight) + 34,
        );
        const stageY = cursorY + laneHeight / 2 - layout.stageHeight / 2;
        const outputIteration = stage.output?.metadata.iteration ?? iteration;
        lanes.push({ height: laneHeight, id: stage.id, order: stageIndex + 1, status: stage.status, y: cursorY });
        nextNodes.push({
          id: stage.id,
          type: "flowNode",
          position: { x: layout.stageX, y: stageY },
          data: {
            active: selectedNodeId === stage.id,
            iteration: outputIteration,
            kind: "stage",
            lang: language,
            order: stageIndex + 1,
            stage: stage.id,
            status: stage.status,
            subtitle: stagePurpose[language][stage.id],
            title: stageLabel[language][stage.id],
          },
        });

        artifacts.forEach((field, fieldIndex) => {
          const nodeId = `${stage.id}:artifact:${field}`;
          nextNodes.push({
            id: nodeId,
            type: "flowNode",
            position: { x: layout.artifactX, y: getRowY(cursorY, laneHeight, fieldIndex, artifacts.length, layout.artifactHeight) },
            data: {
              active: selectedNodeId === nodeId,
              artifactKey: field,
              iteration: outputIteration,
              kind: "artifact",
              lang: language,
              stage: stage.id,
              status: stage.status,
              subtitle: getStageArtifactSummary(stage, field, language),
              title: artifactLabel[language][field] ?? field,
            },
          });
        });

        details.forEach((node, nodeIndex) => {
          const nodeId = `${stage.id}:detail:${node.id}`;
          nextNodes.push({
            id: nodeId,
            type: "flowNode",
            position: { x: layout.detailX, y: getRowY(cursorY, laneHeight, nodeIndex, details.length, layout.detailHeight) },
            data: {
              active: selectedNodeId === nodeId,
              artifactKey: node.sourceArtifact,
              detailId: node.id,
              iteration: outputIteration,
              kind: "detail",
              lang: language,
              stage: stage.id,
              status: stage.status,
              subtitle: node.subtitle,
              title: node.title,
            },
          });
        });

        cursorY += laneHeight + layout.laneGap;
      });
      return { canvasHeight: cursorY + 16, lanes, nodes: nextNodes };
    },
    [iteration, language, selectedNodeId, stages],
  );
  const nodes = treeLayout.nodes;

  const edges = useMemo<Edge[]>(
    () => {
      const sequenceEdges: Edge[] = stageOrder.slice(0, -1).map((stage, index) => ({
        id: `${stage}-${stageOrder[index + 1]}`,
        source: stage,
        sourceHandle: "bottom",
        target: stageOrder[index + 1],
        targetHandle: "top",
        animated: stages[index].status === "running" || stages[index].status === "validating",
        className: stages[index].status === "passed" ? "flow-edge-passed sequence-flow-edge" : "flow-edge sequence-flow-edge",
        type: "smoothstep",
      }));
      const branchEdges = stages.flatMap((stage) => {
        const artifactEdges = stageMeta[stage.id].allowedWrites.map((field) => ({
          id: `${stage.id}->${field}`,
          source: stage.id,
          sourceHandle: "right",
          target: `${stage.id}:artifact:${field}`,
          targetHandle: "left",
          animated: stage.status === "running" || stage.status === "validating",
          className: stage.status === "passed" ? "flow-edge-passed artifact-flow-edge" : "flow-edge artifact-flow-edge",
          type: "smoothstep",
        }));
        const expansionEdges = stage.output && stage.output.metadata.status !== "failed"
          ? getStageExpansionNodes(stage, language).map((node) => ({
              id: `${stage.id}:expansion:${node.id}`,
              source: `${stage.id}:artifact:${node.sourceArtifact}`,
              sourceHandle: "right",
              target: `${stage.id}:detail:${node.id}`,
              targetHandle: "left",
              className: "flow-edge detail-flow-edge",
              type: "smoothstep",
            }))
          : [];
        return [...artifactEdges, ...expansionEdges];
      });
      return [...sequenceEdges, ...branchEdges];
    },
    [language, stages],
  );

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? nodes.find((node) => node.id === activeStage) ?? nodes[0];
  const selectedStage = stages.find((stage) => stage.id === selectedNode?.data.stage) ?? stages[0];
  const selectedPayload = (selectedStage.output?.payload ?? {}) as Record<string, unknown>;
  const contextArtifacts = context as unknown as Record<string, unknown>;
  const inspectorData = selectedNode?.data.kind === "stage"
    ? {
        input: selectedStage.input,
        output: selectedStage.output,
        review: selectedStage.review,
      }
    : selectedNode?.data.artifactKey
      ? selectedPayload[selectedNode.data.artifactKey]
        ?? contextArtifacts[selectedNode.data.artifactKey]
        ?? (selectedNode.data.artifactKey === "reviews" ? selectedStage.review : null)
        ?? selectedStage.output
      : selectedStage.output;

  return (
    <div className="modal-backdrop">
      <section className="tree-modal">
        <div className="modal-titlebar">
          <div>
            <p>{t.stateTree}</p>
            <h2>{t.fullTree}</h2>
          </div>
          <div className="tree-title-actions">
            <span>{language === "zh" ? `第 ${iteration} 轮` : `Iteration ${iteration}`}</span>
            <button className="close-button" type="button" onClick={onClose}>
              <X size={18} />
              {t.close}
            </button>
          </div>
        </div>
        <div className="tree-body">
          <div className="tree-canvas-scroll">
            <div className="tree-canvas" style={{ height: treeLayout.canvasHeight }}>
            <div className="tree-lane-bands" aria-hidden="true">
              {treeLayout.lanes.map((lane) => (
                <div
                  className={`tree-lane-band ${lane.status}`}
                  key={lane.id}
                  style={{ height: lane.height, top: lane.y }}
                >
                  <span>{String(lane.order).padStart(2, "0")}</span>
                </div>
              ))}
            </div>
            <ReactFlow
              edges={edges}
              defaultViewport={{ x: 0, y: 0, zoom: 1 }}
              nodes={nodes}
              nodeTypes={flowNodeTypes}
              nodesConnectable={false}
              nodesDraggable={false}
              onNodeClick={(_, node) => {
                setSelectedNodeId(node.id);
                if (typeof node.data.stage !== "string") return;
                const stage = node.data.stage as StageId;
                onSelectStage(stage);
              }}
              panOnDrag={false}
              preventScrolling={false}
              proOptions={{ hideAttribution: true }}
              zoomOnDoubleClick={false}
              zoomOnPinch={false}
              zoomOnScroll={false}
            />
            </div>
          </div>
          <aside className="tree-inspector">
            <div className="tree-inspector-heading">
              <span>{selectedNode?.data.kind === "stage" ? (language === "zh" ? "阶段详情" : "Stage details") : language === "zh" ? "节点详情" : "Node details"}</span>
              <strong>{selectedNode?.data.title ?? stageLabel[language][selectedStage.id]}</strong>
              <small>
                {stageLabel[language][selectedStage.id]} · {statusLabel[language][selectedStage.status]} · R{selectedNode?.data.iteration ?? iteration}
              </small>
            </div>
            <p>{selectedNode?.data.subtitle ?? stagePurpose[language][selectedStage.id]}</p>
            <div className="tree-inspector-meta">
              <span>{language === "zh" ? "执行单元" : "Executor"}</span>
              <strong>{stageMeta[selectedStage.id].agent}</strong>
            </div>
            <div className="tree-node-actions">
              <button type="button" onClick={() => onNodeAction(selectedStage.id, "only")}>{language === "zh" ? "仅运行" : "Run only"}</button>
              <button type="button" onClick={() => onNodeAction(selectedStage.id, "from")}>{language === "zh" ? "从此继续" : "Continue"}</button>
              <button type="button" onClick={() => onNodeAction(selectedStage.id, "pause")}>{language === "zh" ? "暂停" : "Pause"}</button>
              <button type="button" onClick={() => onNodeAction(selectedStage.id, "history")}>{language === "zh" ? "历史" : "History"}</button>
            </div>
            <pre>{JSON.stringify(inspectorData ?? null, null, 2)}</pre>
          </aside>
        </div>
      </section>
    </div>
  );
}

function FlowNodeCard({ data }: NodeProps<FlowNode>) {
  const { active, iteration, kind, lang, order, status, subtitle, title } = data;
  return (
    <button
      aria-label={`${title} · ${status ? statusLabel[lang][status] : kind}`}
      className={`flow-node ${kind} ${status ?? "queued"} ${active ? "active" : ""}`}
      type="button"
    >
      {kind === "stage" ? <Handle className="node-handle node-handle-top" id="top" position={Position.Top} type="target" /> : null}
      {kind !== "stage" ? <Handle className="node-handle node-handle-left" id="left" position={Position.Left} type="target" /> : null}
      <div className="flow-node-meta">
        <span>{status ? statusLabel[lang][status] : kind}</span>
        {iteration ? <em>R{iteration}</em> : null}
      </div>
      <div className="flow-node-title">
        {order ? <b>{String(order).padStart(2, "0")}</b> : null}
        <strong title={title}>{title}</strong>
      </div>
      <small>{subtitle}</small>
      {kind !== "detail" ? <Handle className="node-handle node-handle-right" id="right" position={Position.Right} type="source" /> : null}
      {kind === "stage" ? <Handle className="node-handle node-handle-bottom" id="bottom" position={Position.Bottom} type="source" /> : null}
    </button>
  );
}

const flowNodeTypes = { flowNode: FlowNodeCard };

function JsonModal({ data, onClose, title }: { data: unknown; onClose: () => void; title: string }) {
  return (
    <div className="modal-backdrop">
      <section className="json-modal">
        <div className="modal-titlebar">
          <h2>{title}</h2>
          <button className="close-button" type="button" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="json-tree-panel">
          <JsonTree data={data} rootLabel="payload" />
        </div>
        <details className="raw-json-panel">
          <summary>Raw JSON</summary>
          <pre className="json-block">
            <code>{JSON.stringify(data, null, 2)}</code>
          </pre>
        </details>
      </section>
    </div>
  );
}

function DocsPage({ language, t }: { language: Language; t: (typeof copy)[Language] }) {
  const items = [t.doc1, t.doc2, t.doc3, t.doc4, t.doc5];
  return (
    <section className="docs-page">
      <div className="docs-hero">
        <p>{language === "zh" ? "使用文档" : "Guide"}</p>
        <h2>{t.docsTitle}</h2>
        <span>{t.docsLead}</span>
      </div>
      <div className="docs-list">
        {items.map((item, index) => (
          <article key={item}>
            <b>{index + 1}</b>
            <p>{item}</p>
          </article>
        ))}
      </div>
      <article className="docs-note">
        <Upload size={18} />
        <div>
          <strong>{t.backendTitle}</strong>
          <p>{t.backendText}</p>
        </div>
      </article>
    </section>
  );
}

function getMessageTitle(message: ThreadMessage, language: Language) {
  if (message.kind === "user") {
    return message.stage ? copy[language].userRevision : copy[language].userQuestion;
  }
  if (!message.stage) {
    return language === "zh" ? "总控" : "Controller";
  }
  if (message.stage === "final_review") {
    return stageLabel[language].final_review;
  }
  return language === "zh" ? `${stageLabel.zh[message.stage]} Agent 输出` : `${stageLabel.en[message.stage]} Agent output`;
}

function getMessageIndexPreview(message: ThreadMessage, language: Language) {
  if (message.body) return trimPreview(message.body, 160);
  if (message.activity) return trimPreview(message.activity, 160);
  if (message.response?.metadata.status === "failed") {
    return trimPreview(
      message.response.self_review.issues[0]
        ?? (language === "zh" ? "该模块执行失败，点击跳转后查看错误详情。" : "This module failed. Open the message for details."),
      160,
    );
  }
  if (message.stage && message.response) {
    const primaryArtifact = stageMeta[message.stage].allowedWrites[0];
    return getStageArtifactSummary({ output: message.response }, primaryArtifact, language);
  }
  if (message.stage) return stagePurpose[language][message.stage];
  return language === "zh" ? "总控调度消息" : "Controller routing message";
}

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatRuntime(durationMs: number, language: Language) {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (language === "zh") {
    if (hours) return `${hours}小时${minutes}分${seconds}秒`;
    if (minutes) return `${minutes}分${seconds}秒`;
    return `${seconds}秒`;
  }
  if (hours) return `${hours}h ${minutes}m ${seconds}s`;
  if (minutes) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function MessageRuntime({
  active,
  createdAt,
  durationMs,
  language,
}: {
  active: boolean;
  createdAt: string;
  durationMs?: number;
  language: Language;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  const startedAt = Date.parse(createdAt);
  const elapsed = active && Number.isFinite(startedAt) ? now - startedAt : durationMs;
  if (elapsed == null) return null;
  return (
    <span className={`message-runtime ${active ? "active" : ""} ${elapsed >= 120_000 ? "long" : ""}`}>
      <Clock3 size={12} />
      {active
        ? language === "zh" ? `已运行 ${formatRuntime(elapsed, language)}` : `Running ${formatRuntime(elapsed, language)}`
        : language === "zh" ? `用时 ${formatRuntime(elapsed, language)}` : `Took ${formatRuntime(elapsed, language)}`}
    </span>
  );
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function objectValue(source: unknown, key: string): unknown {
  if (!source || typeof source !== "object") return undefined;
  const parts = key.split(".");
  let current: unknown = source;
  for (const part of parts) {
    if (!current || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function objectField(source: unknown, key: string): string {
  return stringValue(objectValue(source, key));
}

function objectName(source: unknown): string {
  return objectField(source, "name") || stringValue(source);
}

export default App;
