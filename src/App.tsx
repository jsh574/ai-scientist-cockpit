import {
  Background,
  Controls,
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
  Bot,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  FilePlus2,
  FileJson,
  Globe2,
  GripVertical,
  HelpCircle,
  ListFilter,
  Loader2,
  LockKeyhole,
  MessageSquareText,
  MoreHorizontal,
  Paperclip,
  PanelLeft,
  Plus,
  PlusCircle,
  RotateCcw,
  Send,
  Server,
  SlidersHorizontal,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  createTask,
  executeStage,
  exportTaskBundle,
  fetchArtifacts,
  fetchHealthStatus,
  fetchVersionDiff,
  fetchVersions,
  recordFeedback,
  submitHumanReview,
  type HealthStatus,
  type RemoteArtifact,
  type VersionDiffResult,
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
import type { AgentResponse, EventLog, ReviewRecord, RunMode, StageId, StageRun, TaskContext, UserInput, VersionRecord } from "./types";

type Language = "zh" | "en";
type PageId = "workbench" | "system" | "docs";
type ReasoningLevel = "low" | "medium" | "high" | "ultra";
type ApprovalMode = "ask" | "assist" | "auto";
type MemoryLevel = "low" | "medium" | "high";
type MessageKind = "user" | "agent" | "controller";
type MenuId = "reasoning" | "approval" | "memory" | null;
type ProjectMenuPanel = "root" | "sidebar" | "sort" | null;
type ProjectGroupMode = "project" | "recent" | "time" | "movedown";
type ProjectSortMode = "manual" | "created" | "updated";

interface StarterQuestion {
  domain: string;
  domainLabel: Record<Language, string>;
  question: Record<Language, string>;
}

type FlowNode = Node<FlowNodeData, "flowNode">;

interface FlowNodeData extends Record<string, unknown> {
  active: boolean;
  kind: "stage" | "artifact" | "detail";
  lang: Language;
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

interface ThreadMessage {
  id: string;
  kind: MessageKind;
  stage?: StageId;
  body?: string;
  response?: AgentResponse | null;
  review?: ReviewRecord | null;
  status?: StageRun["status"];
  needsApproval?: boolean;
  retryFromStage?: StageId;
  revisionNote?: string;
  createdAt: string;
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
  files: string[];
  feedbackDrafts: Record<string, string>;
}

const delay = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

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
    newTask: "重置当前项目",
    projects: "项目",
    newProject: "创建新项目",
    archiveAll: "归档所有聊天",
    organizeSidebar: "整理侧边栏",
    sortBy: "排序条件",
    groupByProject: "按项目",
    recentProjects: "近期项目",
    timeOrder: "按时间顺序",
    moveDown: "下移",
    manualSort: "手动排序",
    createdTime: "创建时间",
    recentUpdated: "最近更新",
    stateTree: "状态树",
    fullTree: "完整可视化状态树",
    close: "退出",
    progress: "进度",
    iteration: "轮次",
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
    newTask: "Reset current project",
    projects: "Projects",
    newProject: "New project",
    archiveAll: "Archive all chats",
    organizeSidebar: "Organize sidebar",
    sortBy: "Sort by",
    groupByProject: "By project",
    recentProjects: "Recent projects",
    timeOrder: "By time",
    moveDown: "Move down",
    manualSort: "Manual",
    createdTime: "Created",
    recentUpdated: "Recently updated",
    stateTree: "State tree",
    fullTree: "Full visual state tree",
    close: "Close",
    progress: "Progress",
    iteration: "Round",
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
    feedbackDrafts: {},
  };
}

function deriveProjectTitle(project: ProjectSession, messages: ThreadMessage[], questionDraft: string) {
  const firstQuestion = messages.find((message) => message.kind === "user" && !message.stage)?.body;
  if (firstQuestion) return truncateTitle(firstQuestion, project.title);
  if (project.messages.length === 0 && questionDraft.trim()) return truncateTitle(questionDraft, project.title);
  return project.title;
}

function App() {
  const [language, setLanguage] = useState<Language>("zh");
  const [page, setPage] = useState<PageId>("workbench");
  const [reasoning, setReasoning] = useState<ReasoningLevel>("ultra");
  const [approval, setApproval] = useState<ApprovalMode>("assist");
  const [memory, setMemory] = useState<MemoryLevel>("medium");
  const [openMenu, setOpenMenu] = useState<MenuId>(null);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [projectSubmenu, setProjectSubmenu] = useState<ProjectMenuPanel>(null);
  const [projectGroupMode, setProjectGroupMode] = useState<ProjectGroupMode>("project");
  const [projectSortMode, setProjectSortMode] = useState<ProjectSortMode>("manual");
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
  const [files, setFiles] = useState<string[]>(() => projects[0].files);
  const [feedbackDrafts, setFeedbackDrafts] = useState<Record<string, string>>(() => projects[0].feedbackDrafts);
  const [remoteArtifacts, setRemoteArtifacts] = useState<RemoteArtifact[]>([]);
  const [runtimeError, setRuntimeError] = useState("");
  const [versionDiff, setVersionDiff] = useState<VersionDiffResult | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const taskIdRef = useRef(context.task_id);

  const t = copy[language];
  const runMode = approvalToRunMode[approval];
  const completedCount = stages.filter((stage) => stage.status === "passed").length;
  const finished = context.current_stage === "completed";
  const progress = Math.round((completedCount / stages.length) * 100);
  const currentStageLabel = finished ? statusLabel[language].completed : stageLabel[language][activeStage];
  const uploadedLabel = files.length ? files.join(", ") : t.noFiles;
  const latestEvent = events[0]?.message;
  const maxIterations = health?.max_iterations ?? 10;
  const readyAgentCount = health
    ? Object.entries(health.sources).filter(([stage, source]) => stage !== "artifact_service" && source.available).length
    : 0;
  const activeProject = projects.find((project) => project.id === activeProjectId) ?? projects[0];
  const sortedProjects = useMemo(() => {
    const list = [...projects];
    if (projectSortMode === "created" || projectGroupMode === "time") {
      list.sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
    }
    if (projectSortMode === "updated" || projectGroupMode === "recent") {
      list.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
    }
    return list;
  }, [projectGroupMode, projectSortMode, projects]);

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
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, running]);

  useEffect(() => {
    const now = new Date().toISOString();
    setProjects((current) =>
      current.map((project) =>
        project.id === activeProjectId
          ? {
              ...project,
              activeStage,
              context,
              events,
              feedbackDrafts,
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
    context,
    events,
    feedbackDrafts,
    files,
    hasSubmittedQuestion,
    messages,
    pendingIndex,
    questionDraft,
    reviewStage,
    stages,
    versions,
  ]);

  const hydrateProject = useCallback((project: ProjectSession) => {
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
    setFeedbackDrafts(project.feedbackDrafts);
    setRemoteArtifacts([]);
    setRuntimeError("");
    setVersionDiff(null);
    setRunning(false);
    setTreeOpen(false);
    setJsonOpen(null);
  }, []);

  const selectProject = useCallback(
    (projectId: string) => {
      if (running || projectId === activeProjectId) return;
      const nextProject = projects.find((project) => project.id === projectId);
      if (!nextProject) return;
      setActiveProjectId(projectId);
      hydrateProject(nextProject);
      setPage("workbench");
    },
    [activeProjectId, hydrateProject, projects, running],
  );

  const createNewProject = useCallback(() => {
    if (running) return;
    const nextProject = createProjectSession(projects.length + 1, runMode, language);
    setProjects((current) => [...current, nextProject]);
    setActiveProjectId(nextProject.id);
    hydrateProject(nextProject);
    setPage("workbench");
    setProjectMenuOpen(false);
    setProjectSubmenu(null);
  }, [hydrateProject, language, projects.length, runMode, running]);

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

  const pushMessage = useCallback((message: Omit<ThreadMessage, "id" | "createdAt">) => {
    const id = makeMessageId(message.kind);
    setMessages((current) => [...current, { ...message, id, createdAt: new Date().toISOString() }]);
    return id;
  }, []);

  const patchMessage = useCallback((id: string, patch: Partial<ThreadMessage>) => {
    setMessages((current) => current.map((message) => (message.id === id ? { ...message, ...patch } : message)));
  }, []);

  const updateStage = useCallback((stageId: StageId, patch: Partial<StageRun>) => {
    setStages((current) => current.map((stage) => (stage.id === stageId ? { ...stage, ...patch } : stage)));
  }, []);

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
          },
        },
      };
    },
    [context.user_input.user_constraints, language, runMode],
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

  const resetDemo = useCallback(() => {
    const fresh = createInitialContext(runMode);
    taskIdRef.current = fresh.task_id;
    setRunning(false);
    setContext(fresh);
    setStages(createInitialStages(fresh));
    setEvents(seedEvents);
    setVersions([]);
    setMessages([]);
    setQuestionDraft("");
    setHasSubmittedQuestion(false);
    setFiles([]);
    setReviewStage(null);
    setPendingIndex(null);
    setFeedbackDrafts({});
    setActiveStage("question_understanding");
  }, [runMode]);

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
        updateStage(stage, {
          status: "validating",
          output: response,
          duration: `${((performance.now() - startTime) / 1000).toFixed(1)}s`,
        });
        patchMessage(messageId, { status: "validating", response });
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
        body: language === "zh" ? `任务创建失败：${message}` : `Task creation failed: ${message}`,
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

    pushMessage({ kind: "user", body: question });
    pushMessage({ kind: "controller", body: t.controllerStarted, status: "passed" });
    appendEvent(
      "task_started",
      `总控已启动：推理 ${reasoning}，权限 ${approval}，记忆 ${memory}。`,
      `Controller started: reasoning ${reasoning}, access ${approval}, memory ${memory}.`,
    );
    await continueFrom(0, fresh, []);
  }, [appendEvent, approval, buildFreshTask, continueFrom, language, memory, pushMessage, questionDraft, reasoning, running, t.controllerStarted]);

  const approveReview = useCallback(async (stageOverride?: StageId) => {
    const stage = stageOverride ?? reviewStage;
    if (!stage || running) return;
    const resumeIndex = pendingIndex ?? stageOrder.indexOf(stage);
    if (resumeIndex < 0) return;
    const stageRun = stages.find((item) => item.id === stage);
    if (!stageRun?.output) return;

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
      remoteResult = await submitHumanReview(context.task_id, stage, "accept", comment);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      appendEvent("human_review_failed", `审批提交失败：${detail}`, `Review submission failed: ${detail}`, stage);
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
    await delay(260);
    await continueFrom(resumeIndex + 1, nextContext, nextVersions);
  }, [appendEvent, context, continueFrom, language, pendingIndex, reviewStage, running, stages, updateStage, versions]);

  const rerunStageWithFeedback = useCallback(
    async (
      stage: StageId,
      sourceMessageId: string,
      systemGenerated = false,
      response: AgentResponse | null = null,
      review: ReviewRecord | null = null,
    ) => {
      if (running) return;
      const index = stageOrder.indexOf(stage);
      if (index < 0) return;
      const note = systemGenerated && response
        ? buildSystemRevisionNote(response, review, language)
        : feedbackDrafts[sourceMessageId]?.trim() || (language === "zh" ? "请重新检查并改进这一阶段输出。" : "Please re-check and improve this stage output.");

      let rerunContext = context;
      let rerunVersions = versions;
      try {
        const recorded = await recordFeedback(context.task_id, stage, note);
        if (recorded) {
          rerunContext = recorded;
          rerunVersions = recorded.versions;
          setContext(recorded);
          setVersions(recorded.versions);
        }
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        appendEvent("feedback_record_failed", `反馈保存失败：${detail}`, `Feedback persistence failed: ${detail}`, stage);
        setRuntimeError(detail);
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
      updateStage(stage, { status: "retrying", review: createReviewRecord(stage, "retry") });
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
      await delay(320);
      await continueFrom(index, rerunContext, rerunVersions, note);
    },
    [appendEvent, context, continueFrom, feedbackDrafts, language, pushMessage, running, updateStage, versions],
  );

  const activeStageRun = stages.find((stage) => stage.id === activeStage) ?? stages[0];

  return (
    <div className="app-shell">
      <MessageIndexRail language={language} messages={messages} />

      <aside className={`control-rail ${projectMenuOpen ? "menu-open" : ""}`}>
        <div className="brand-row">
          <span className="brand-mark">
            <Bot size={18} />
          </span>
          <div>
            <strong>{t.appName}</strong>
            <small>{t.appSub} · {t.productHint}</small>
          </div>
        </div>

        <ProjectPanel
          activeProjectId={activeProject.id}
          disabled={running}
          groupMode={projectGroupMode}
          language={language}
          menuOpen={projectMenuOpen}
          onArchiveAll={() => {
            setProjectMenuOpen(false);
            setProjectSubmenu(null);
          }}
          onCreateProject={createNewProject}
          onGroupModeChange={setProjectGroupMode}
          onMenuOpenChange={setProjectMenuOpen}
          onSelectProject={selectProject}
          onSortModeChange={setProjectSortMode}
          onSubmenuChange={setProjectSubmenu}
          projects={sortedProjects}
          sortMode={projectSortMode}
          submenu={projectSubmenu}
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
            <StatusMetric label={t.iteration} value={`R${context.iteration}/${maxIterations}`} />
          </div>
          {latestEvent ? <p className="latest-event">{latestEvent}</p> : null}
          <button className="ghost-button" type="button" onClick={resetDemo}>
            <RotateCcw size={15} />
            {t.newTask}
          </button>
        </section>
      </aside>

      <main className="thread-shell">
        {page === "workbench" ? (
          <>
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

            <section className="thread-area" aria-label={language === "zh" ? "对话记录" : "Conversation"}>
              {messages.length === 0 ? (
                <ResearchStarter
                  constraints={context.user_input.user_constraints}
                  health={health}
                  language={language}
                  maxIterations={maxIterations}
                  onConstraintChange={updateConstraints}
                  onSelectQuestion={(starter) => {
                    setQuestionDraft(starter.question[language]);
                    updateConstraints({ domain_preference: starter.domain });
                  }}
                />
              ) : null}

              <div className="message-list">
                {messages.map((message) => (
                  <ThreadMessageCard
                    feedbackValue={feedbackDrafts[message.id] ?? ""}
                    key={message.id}
                    language={language}
                    message={message}
                    onApprove={() => message.stage && void approveReview(message.stage)}
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
                    onSelectStage={(stage) => setActiveStage(stage)}
                    running={running}
                    t={t}
                  />
                ))}
                <div ref={threadEndRef} />
              </div>
            </section>

            <section className="composer-shell">
              <div className="composer">
                <textarea
                  aria-label={t.questionPlaceholder}
                  disabled={running || context.current_stage === "human_review"}
                  onChange={(event) => setQuestionDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void startDemo();
                    }
                  }}
                  placeholder={!hasSubmittedQuestion ? t.questionPlaceholder : ""}
                  value={questionDraft}
                />
                <div className="composer-footer">
                  <label className="attach-button">
                    <input
                      multiple
                      type="file"
                      onChange={(event) => {
                        const nextFiles = Array.from(event.target.files ?? []).map((file) => file.name);
                        setFiles(nextFiles);
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
                    openMenu={openMenu}
                    reasoning={reasoning}
                    setApproval={setApproval}
                    setMemory={setMemory}
                    setOpenMenu={setOpenMenu}
                    setReasoning={setReasoning}
                    t={t}
                  />

                  <button
                    className="send-button"
                    disabled={running || context.current_stage === "human_review" || !questionDraft.trim()}
                    type="button"
                    onClick={startDemo}
                    title={t.start}
                  >
                    {running ? <Loader2 className="spin" size={17} /> : <Send size={17} />}
                  </button>
                </div>
              </div>
            </section>
          </>
        ) : page === "system" ? (
          <SystemPage
            artifacts={remoteArtifacts}
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
          language={language}
          onClose={() => setTreeOpen(false)}
          setActiveStage={setActiveStage}
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
  onConstraintChange,
  onSelectQuestion,
}: {
  constraints: UserInput["user_constraints"];
  health: HealthStatus | null;
  language: Language;
  maxIterations: number;
  onConstraintChange: (patch: Partial<UserInput["user_constraints"]>) => void;
  onSelectQuestion: (starter: StarterQuestion) => void;
}) {
  const zh = language === "zh";
  const selectedDomain = domainOptions.includes(constraints.domain_preference as (typeof domainOptions)[number])
    ? (constraints.domain_preference as (typeof domainOptions)[number])
    : "general";
  const readyAgents = health
    ? Object.entries(health.sources).filter(([stage, source]) => stage !== "artifact_service" && source.available).length
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
          {health ? (zh ? "总控在线" : "Controller online") : zh ? "等待后端" : "Backend offline"}
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
            {health ? ` · ${health.model} · ${health.real_agent_stages.length} Agents` : ""}
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
        <StatusMetric label={zh ? "迭代" : "Iteration"} value={`R${context.iteration}/${maxIterations}`} />
        <StatusMetric label={zh ? "版本" : "Versions"} value={String(versions.length)} />
        <StatusMetric label={zh ? "事件" : "Events"} value={String(events.length)} />
      </div>

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
            <div className="runtime-row" key={`${api.method}-${api.path}`}>
              <b>{api.method}</b>
              <code>{api.path}</code>
              <span className={`api-state ${api.status}`}>{api.status}</span>
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

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
  return (
    <aside className="message-index" aria-label={language === "zh" ? "消息索引" : "Message index"}>
      <div className="index-stack">
        {messages.map((message, index) => {
          const label = getMessageTitle(message, language);
          return (
            <button
              aria-label={label}
              className={`index-tick ${message.kind} ${message.status ?? ""}`}
              key={message.id}
              type="button"
              onClick={() => document.getElementById(message.id)?.scrollIntoView({ behavior: "smooth", block: "center" })}
            >
              <span />
              <strong>{String(index + 1).padStart(2, "0")}</strong>
              <em>{label}</em>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

const projectMenuWidth = 244;
const projectMenuGap = 8;
const viewportMargin = 12;

function clampMenuPosition(value: number, width: number) {
  if (typeof window === "undefined") return value;
  const max = Math.max(viewportMargin, window.innerWidth - width - viewportMargin);
  return Math.min(Math.max(value, viewportMargin), max);
}

function ProjectPanel({
  activeProjectId,
  disabled,
  groupMode,
  menuOpen,
  onArchiveAll,
  onCreateProject,
  onGroupModeChange,
  onMenuOpenChange,
  onSelectProject,
  onSortModeChange,
  onSubmenuChange,
  projects,
  sortMode,
  submenu,
  t,
}: {
  activeProjectId: string;
  disabled: boolean;
  groupMode: ProjectGroupMode;
  language: Language;
  menuOpen: boolean;
  onArchiveAll: () => void;
  onCreateProject: () => void;
  onGroupModeChange: (value: ProjectGroupMode) => void;
  onMenuOpenChange: (value: boolean) => void;
  onSelectProject: (projectId: string) => void;
  onSortModeChange: (value: ProjectSortMode) => void;
  onSubmenuChange: (value: ProjectMenuPanel) => void;
  projects: ProjectSession[];
  sortMode: ProjectSortMode;
  submenu: ProjectMenuPanel;
  t: (typeof copy)[Language];
}) {
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const [menuPosition, setMenuPosition] = useState({ left: 0, top: 0 });

  useEffect(() => {
    if (!menuOpen) return;

    const updateMenuPosition = () => {
      const triggerRect = menuTriggerRef.current?.getBoundingClientRect();
      if (triggerRect) {
        setMenuPosition({
          left: clampMenuPosition(triggerRect.right - projectMenuWidth + 4, projectMenuWidth),
          top: triggerRect.bottom + projectMenuGap,
        });
      }
    };

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [menuOpen]);

  const menuLayer =
    menuOpen && typeof document !== "undefined"
      ? createPortal(
          <div className="project-menu" style={{ left: menuPosition.left, top: menuPosition.top }}>
            <button type="button" onClick={onArchiveAll}>
              <Archive size={16} />
              <span>{t.archiveAll}</span>
            </button>
            <button
              className={submenu === "sidebar" ? "hovered" : ""}
              type="button"
              onClick={() => onSubmenuChange(submenu === "sidebar" ? null : "sidebar")}
            >
              <PanelLeft size={16} />
              <span>{t.organizeSidebar}</span>
              <ChevronRight size={16} />
            </button>
            <button
              className={submenu === "sort" ? "hovered" : ""}
              type="button"
              onClick={() => onSubmenuChange(submenu === "sort" ? null : "sort")}
            >
              <ListFilter size={16} />
              <span>{t.sortBy}</span>
              <ChevronRight size={16} />
            </button>
            {submenu === "sidebar" ? (
              <div className="project-submenu sidebar-submenu">
                <button type="button" onClick={() => onGroupModeChange("project")}>
                  <MessageSquareText size={16} />
                  <span>{t.groupByProject}</span>
                  {groupMode === "project" ? <Check size={17} /> : null}
                </button>
                <button type="button" onClick={() => onGroupModeChange("recent")}>
                  <Clock3 size={16} />
                  <span>{t.recentProjects}</span>
                  {groupMode === "recent" ? <Check size={17} /> : null}
                </button>
                <button type="button" onClick={() => onGroupModeChange("time")}>
                  <Clock3 size={16} />
                  <span>{t.timeOrder}</span>
                  {groupMode === "time" ? <Check size={17} /> : null}
                </button>
                <button type="button" onClick={() => onGroupModeChange("movedown")}>
                  <ArrowDown size={16} />
                  <span>{t.moveDown}</span>
                  {groupMode === "movedown" ? <Check size={17} /> : null}
                </button>
              </div>
            ) : null}
            {submenu === "sort" ? (
              <div className="project-submenu sort-submenu">
                <button type="button" onClick={() => onSortModeChange("manual")}>
                  <GripVertical size={16} />
                  <span>{t.manualSort}</span>
                  {sortMode === "manual" ? <Check size={17} /> : null}
                </button>
                <button type="button" onClick={() => onSortModeChange("created")}>
                  <PlusCircle size={16} />
                  <span>{t.createdTime}</span>
                  {sortMode === "created" ? <Check size={17} /> : null}
                </button>
                <button type="button" onClick={() => onSortModeChange("updated")}>
                  <SlidersHorizontal size={16} />
                  <span>{t.recentUpdated}</span>
                  {sortMode === "updated" ? <Check size={17} /> : null}
                </button>
              </div>
            ) : null}
          </div>,
          document.body,
        )
      : null;

  return (
    <section className="project-panel">
      <div className="project-titlebar">
        <button className="project-title-button" type="button" onClick={() => onSubmenuChange(submenu === "root" ? null : "root")}>
          <span>{t.projects}</span>
          <ChevronDown size={15} />
        </button>
        <div className="project-actions">
          <button
            aria-label={t.organizeSidebar}
            className={`icon-quiet ${menuOpen ? "active" : ""}`}
            ref={menuTriggerRef}
            type="button"
            onClick={() => {
              onMenuOpenChange(!menuOpen);
              onSubmenuChange(null);
            }}
          >
            <MoreHorizontal size={17} />
          </button>
          <button aria-label={t.newProject} className="icon-quiet" disabled={disabled} type="button" onClick={onCreateProject}>
            <FilePlus2 size={17} />
          </button>
        </div>
        {menuLayer}
      </div>

      <div className="project-list">
        {projects.map((project) => (
          <button
            className={`project-item ${project.id === activeProjectId ? "active" : ""}`}
            disabled={disabled}
            key={project.id}
            type="button"
            onClick={() => onSelectProject(project.id)}
            title={project.title}
          >
            <MessageSquareText size={16} />
            <span>{project.title}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ControllerSettings({
  approval,
  language,
  memory,
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
        footerLabel="GPT-5.5"
        icon={<Brain size={15} />}
        id="reasoning"
        label={`${language === "zh" ? "5.5" : "5.5"} ${reasoningOptions.find((option) => option.value === reasoning)?.label ?? ""}`}
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
  footerLabel,
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
  footerLabel?: string;
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
          {footerLabel ? (
            <button className="menu-footer" type="button" onClick={() => onOpenChange(null)}>
              <span>{footerLabel}</span>
              <ChevronRight size={15} />
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ThreadMessageCard({
  feedbackValue,
  language,
  message,
  onApprove,
  onFeedbackChange,
  onOpenJson,
  onRerun,
  onSelectStage,
  running,
  t,
}: {
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
          <p>{message.body}</p>
        </div>
      </article>
    );
  }

  const title = getMessageTitle(message, language);
  const stage = message.stage;

  return (
    <article className={`thread-message ${message.kind}-message ${message.status ?? ""}`} id={message.id}>
      <div className="message-avatar">{message.kind === "controller" ? <Bot size={17} /> : <Sparkles size={17} />}</div>
      <div className="message-bubble">
        <header>
          <div>
            <strong>{title}</strong>
            {stage ? <small>{stageMeta[stage].agent}</small> : null}
          </div>
          <span className={`state-chip ${message.status ?? "queued"}`}>{statusLabel[language][message.status ?? "queued"]}</span>
        </header>

        {message.body ? <p className="message-copy">{message.body}</p> : null}
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
              <span>Gate {message.review ? Math.round(message.review.overall_score * 100) : "--"}%</span>
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
              <button className="main-action" disabled={running} type="button" onClick={onApprove}>
                <CheckCircle2 size={16} />
                {t.approveContinue}
              </button>
            </div>
            <div className="revision-composer">
              <textarea
                disabled={running}
                onChange={(event) => onFeedbackChange(event.target.value)}
                placeholder={t.revisePlaceholder}
                value={feedbackValue}
              />
              <button className="ghost-button" disabled={running} type="button" onClick={onRerun}>
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
                <button className="main-action" disabled={running} type="button" onClick={onApprove}>
                  <CheckCircle2 size={16} />
                  {language === "zh" ? "继续执行" : "Continue"}
                </button>
                <button className="ghost-button" disabled={running} type="button" onClick={onRerun}>
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
    return (
      <div className="module-output">
        <BulletList
          label={language === "zh" ? "文献卡片" : "Literature cards"}
          values={arrayValue(payload.literature_cards).map((item) => `${objectField(item, "title")} · ${objectField(item, "year")}`)}
        />
        <BulletList label={language === "zh" ? "证据卡片" : "Evidence cards"} values={arrayValue(payload.evidence_cards).map((item) => objectField(item, "claim"))} />
        <BulletList label={language === "zh" ? "知识空白" : "Knowledge gaps"} values={arrayValue(payload.knowledge_gaps).map((item) => objectField(item, "description"))} />
      </div>
    );
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
    const plan = ((payload.research_plan as Record<string, unknown>)?.plans as Array<Record<string, unknown>> | undefined)?.[0]?.plan as Record<string, unknown> | undefined;
    return (
      <div className="module-output">
        <KeyValue label={language === "zh" ? "研究问题" : "Problem"} value={stringValue(plan?.problem_statement)} />
        <PillList label={language === "zh" ? "方法" : "Methods"} values={arrayValue(objectValue(plan?.technical_details, "required_methods"))} />
        <PillList label={language === "zh" ? "数据字段" : "Data fields"} values={arrayValue((objectValue(plan?.datasets, "target") as Array<Record<string, unknown>> | undefined)?.[0]?.fields)} />
        <BulletList label={language === "zh" ? "失败判据" : "Falsification criteria"} values={arrayValue(objectValue(plan?.results, "falsification_criteria"))} />
        <BulletList label={language === "zh" ? "反馈任务" : "Feedback tasks"} values={arrayValue(plan?.feedback_tasks).map((item) => objectField(item, "objective"))} />
      </div>
    );
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

function StatusMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="status-metric">
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

function getStageArtifactSummary(stage: StageRun, field: string, language: Language) {
  const payload = (stage.output?.payload ?? {}) as Record<string, unknown>;
  if (!stage.output) return language === "zh" ? "等待该模块输出后写入 task_context" : "Waiting for this module output";

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
  language,
  onClose,
  setActiveStage,
  stages,
  t,
}: {
  activeStage: StageId;
  language: Language;
  onClose: () => void;
  setActiveStage: (stage: StageId) => void;
  stages: StageRun[];
  t: (typeof copy)[Language];
}) {
  const nodes = useMemo<FlowNode[]>(
    () => {
      const nextNodes: FlowNode[] = [];
      const layout = {
        artifactHeight: 72,
        artifactX: 378,
        detailHeight: 82,
        detailX: 730,
        laneGap: 18,
        minLaneHeight: 264,
        rowGap: 104,
        stageHeight: 96,
        stageX: 64,
      };
      let cursorY = 30;
      const getRowY = (laneTop: number, laneHeight: number, rowIndex: number, totalRows: number, nodeHeight: number) => {
        const blockHeight = (totalRows - 1) * layout.rowGap;
        return laneTop + laneHeight / 2 - blockHeight / 2 + rowIndex * layout.rowGap - nodeHeight / 2;
      };

      stages.forEach((stage) => {
        const artifacts = stageMeta[stage.id].allowedWrites;
        const details = stage.output ? getStageExpansionNodes(stage, language) : [];
        const rowCount = Math.max(1, artifacts.length, details.length);
        const laneHeight = Math.max(layout.minLaneHeight, rowCount * layout.rowGap + 20);
        const stageY = cursorY + laneHeight / 2 - layout.stageHeight / 2;
        nextNodes.push({
          id: stage.id,
          type: "flowNode",
          position: { x: layout.stageX, y: stageY },
          data: {
            active: stage.id === activeStage,
            kind: "stage",
            lang: language,
            stage: stage.id,
            status: stage.status,
            subtitle: stagePurpose[language][stage.id],
            title: stageLabel[language][stage.id],
          },
        });

        artifacts.forEach((field, fieldIndex) => {
          nextNodes.push({
            id: `${stage.id}:${field}`,
            type: "flowNode",
            position: { x: layout.artifactX, y: getRowY(cursorY, laneHeight, fieldIndex, artifacts.length, layout.artifactHeight) },
            data: {
              active: false,
              kind: "artifact",
              lang: language,
              stage: stage.id,
              status: stage.status,
              subtitle: getStageArtifactSummary(stage, field, language),
              title: field,
            },
          });
        });

        details.forEach((node, nodeIndex) => {
          nextNodes.push({
            id: `${stage.id}:detail:${node.id}`,
            type: "flowNode",
            position: { x: layout.detailX, y: getRowY(cursorY, laneHeight, nodeIndex, details.length, layout.detailHeight) },
            data: {
              active: false,
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
      return nextNodes;
    },
    [activeStage, language, stages],
  );

  const edges = useMemo<Edge[]>(
    () => {
      const sequenceEdges: Edge[] = stageOrder.slice(0, -1).map((stage, index) => ({
        id: `${stage}-${stageOrder[index + 1]}`,
        source: stage,
        sourceHandle: "bottom",
        target: stageOrder[index + 1],
        targetHandle: "top",
        animated: stages[index].status === "running" || stages[index].status === "validating",
        className: stages[index].status === "passed" ? "flow-edge-passed vertical-flow-edge" : "flow-edge vertical-flow-edge",
        type: "smoothstep",
      }));
      const branchEdges = stages.flatMap((stage) => {
        const artifactEdges = stageMeta[stage.id].allowedWrites.map((field) => ({
          id: `${stage.id}->${field}`,
          source: stage.id,
          sourceHandle: "right",
          target: `${stage.id}:${field}`,
          targetHandle: "left",
          animated: stage.status === "running" || stage.status === "validating",
          className: stage.status === "passed" ? "flow-edge-passed branch-flow-edge" : "flow-edge branch-flow-edge",
          type: "smoothstep",
        }));
        const expansionEdges = stage.output
          ? getStageExpansionNodes(stage, language).map((node) => ({
              id: `${stage.id}:expansion:${node.id}`,
              source: `${stage.id}:${node.sourceArtifact}`,
              sourceHandle: "right",
              target: `${stage.id}:detail:${node.id}`,
              targetHandle: "left",
              className: "flow-edge branch-flow-edge faint-flow-edge",
              type: "smoothstep",
            }))
          : [];
        return [...artifactEdges, ...expansionEdges];
      });
      return [...sequenceEdges, ...branchEdges];
    },
    [language, stages],
  );

  return (
    <div className="modal-backdrop">
      <section className="tree-modal">
        <div className="modal-titlebar">
          <div>
            <p>{t.stateTree}</p>
            <h2>{t.fullTree}</h2>
          </div>
          <button className="close-button" type="button" onClick={onClose}>
            <X size={18} />
            {t.close}
          </button>
        </div>
        <div className="tree-canvas">
          <ReactFlow
            edges={edges}
            fitView
            fitViewOptions={{ padding: 0.12 }}
            maxZoom={1.2}
            minZoom={0.25}
            nodes={nodes}
            nodeTypes={flowNodeTypes}
            nodesDraggable={false}
            onNodeClick={(_, node) => {
              if (typeof node.data.stage === "string") setActiveStage(node.data.stage as StageId);
            }}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#d8dee7" gap={22} size={1} />
            <Controls position="bottom-right" showInteractive={false} />
          </ReactFlow>
        </div>
      </section>
    </div>
  );
}

function FlowNodeCard({ data }: NodeProps<FlowNode>) {
  const { active, kind, lang, status, subtitle, title } = data;
  return (
    <button className={`flow-node ${kind} ${status ?? "queued"} ${active ? "active" : ""}`} type="button">
      {kind === "stage" ? <Handle className="node-handle node-handle-top" id="top" position={Position.Top} type="target" /> : null}
      {kind !== "stage" ? <Handle className="node-handle node-handle-left" id="left" position={Position.Left} type="target" /> : null}
      <span>{status ? statusLabel[lang][status] : kind}</span>
      <strong>{title}</strong>
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
        <pre className="json-block">
          <code>{JSON.stringify(data, null, 2)}</code>
        </pre>
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

function formatTime(value: string) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
