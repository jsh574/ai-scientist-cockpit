import type {
  AgentResponse,
  ApiSpec,
  ArtifactItem,
  EventLog,
  FinalReview,
  ResearchPlan,
  ReviewRecord,
  RunMode,
  StageId,
  StageRun,
  TaskContext,
  VersionRecord,
} from "./types";

export const stageOrder: StageId[] = [
  "question_understanding",
  "knowledge_integration",
  "hypothesis_generation",
  "evidence_mapping",
  "research_planning",
  "final_review",
];

export const manualGateStages: StageId[] = [
  "question_understanding",
  "hypothesis_generation",
  "research_planning",
];

export const stageMeta: Record<
  StageId,
  {
    label: string;
    agent: string;
    description: string;
    allowedWrites: string[];
  }
> = {
  question_understanding: {
    label: "问题理解",
    agent: "Question Understanding Agent",
    description: "将原始科学问题转成可检索、可验证、可迭代的 question_card。",
    allowedWrites: ["question_card"],
  },
  knowledge_integration: {
    label: "知识整合",
    agent: "Knowledge Integration Agent",
    description: "根据 question_card 整理文献、证据和知识空白。",
    allowedWrites: ["literature_cards", "evidence_cards", "knowledge_gaps"],
  },
  hypothesis_generation: {
    label: "候选假设生成",
    agent: "Hypothesis Generation Agent",
    description: "基于证据和知识空白生成候选科学假设。",
    allowedWrites: ["hypothesis_cards"],
  },
  evidence_mapping: {
    label: "证据梳理",
    agent: "Evidence Mapping Agent",
    description: "把假设与支持、反对、不确定证据绑定，形成证据链。",
    allowedWrites: ["evidence_map", "reviews"],
  },
  research_planning: {
    label: "研究计划输出",
    agent: "Experiment Planner Agent",
    description: "为较优假设生成变量、方法、数据、指标和失败判据。",
    allowedWrites: ["research_plan"],
  },
  final_review: {
    label: "总控最终审核",
    agent: "Orchestrator Review Gate",
    description: "从完整 task_context 检查闭环、证据追溯和迭代价值。",
    allowedWrites: ["final_review", "versions"],
  },
};

const baseUserInput = {
  original_question: "阿尔茨海默病的关键致病机制是什么？",
  user_constraints: {
    language: "zh-CN",
    domain_preference: "biomedicine",
    max_hypotheses: 5,
    output_detail_level: "standard" as const,
    reasoning_level: "high" as const,
    memory_level: "medium" as const,
  },
};

export const questionCard = {
  question_id: "q_001",
  original_question: baseUserInput.original_question,
  core_question: "神经炎症是否通过促进 Tau 病理扩散，加速阿尔茨海默病认知功能下降？",
  question_type: "mechanism_analysis",
  domain: ["神经科学", "医学", "分子生物学"],
  research_object: {
    name: "阿尔茨海默病",
    type: "disease",
    aliases: ["Alzheimer's disease", "AD"],
  },
  key_concepts: [
    {
      name: "Aβ沉积",
      normalized_name: "amyloid beta deposition",
      category: "pathological_process",
    },
    {
      name: "Tau蛋白异常",
      normalized_name: "tau pathology",
      category: "molecular_mechanism",
    },
    {
      name: "神经炎症",
      normalized_name: "neuroinflammation",
      category: "cellular_process",
    },
    {
      name: "认知下降",
      normalized_name: "cognitive decline",
      category: "clinical_endpoint",
    },
  ],
  key_variables: [
    { name: "神经炎症", type: "causal_factor", role: "potential_driver" },
    { name: "Tau蛋白异常", type: "mediator", role: "disease_progression_factor" },
    { name: "认知下降", type: "outcome", role: "clinical_endpoint" },
  ],
  sub_questions: [
    {
      sub_question_id: "sq_001",
      content: "神经炎症标志物是否先于或伴随 Tau 病理扩散出现？",
    },
    {
      sub_question_id: "sq_002",
      content: "Tau 病理扩散是否比 Aβ 负荷更能预测认知下降速度？",
    },
    {
      sub_question_id: "sq_003",
      content: "炎症反应是病理驱动因素，还是病理结果的伴随现象？",
    },
  ],
  research_scope: {
    included: ["机制解释", "候选假设生成", "证据链构建", "可验证研究计划"],
    excluded: ["个体诊断", "临床用药建议", "治疗方案推荐"],
  },
  search_keywords: {
    zh: ["阿尔茨海默病", "神经炎症", "Tau病理", "认知下降", "纵向队列"],
    en: [
      "Alzheimer disease neuroinflammation",
      "tau propagation",
      "cognitive decline",
      "longitudinal cohort",
      "microglia activation",
    ],
  },
};

export const literatureCards = [
  {
    literature_id: "lit_001",
    title: "Neuroinflammation and tau pathology in Alzheimer's disease",
    authors: ["Demo Author A", "Demo Author B"],
    year: 2024,
    source: "Nature Reviews Neuroscience",
    doi: "10.demo/nrn.2024.001",
    url: "https://example.org/lit_001",
    literature_type: "review",
    relevance_score: 0.92,
    main_findings: [
      "小胶质细胞激活与 Tau 病理负荷存在稳定相关。",
      "炎症信号可能参与 Tau 传播，但因果链仍需验证。",
    ],
    related_concepts: ["神经炎症", "Tau蛋白异常", "认知下降"],
  },
  {
    literature_id: "lit_002",
    title: "Longitudinal biomarkers of cognitive decline in AD cohorts",
    authors: ["Demo Author C", "Demo Author D"],
    year: 2023,
    source: "Alzheimer's & Dementia",
    doi: "10.demo/alz.2023.002",
    url: "https://example.org/lit_002",
    literature_type: "cohort_study",
    relevance_score: 0.88,
    main_findings: [
      "Tau PET 指标比单次 Aβ 指标更直接预测认知下降。",
      "炎症标志物在不同人群中的效应方向存在差异。",
    ],
    related_concepts: ["Tau蛋白异常", "认知下降", "纵向数据"],
  },
];

export const evidenceCards = [
  {
    evidence_id: "ev_001",
    claim: "Tau 病理负荷与认知下降程度存在较强相关性。",
    source_literature_id: "lit_002",
    evidence_type: "observational_result",
    support_direction: "support" as const,
    related_concepts: ["Tau蛋白异常", "认知下降"],
    strength_score: 0.86,
    summary: "纵向队列显示 Tau PET 变化与认知评分下降速度相关。",
  },
  {
    evidence_id: "ev_002",
    claim: "神经炎症标志物与 Tau 病理空间分布存在重叠。",
    source_literature_id: "lit_001",
    evidence_type: "literature_finding",
    support_direction: "support" as const,
    related_concepts: ["神经炎症", "Tau蛋白异常"],
    strength_score: 0.79,
    summary: "多项影像和脑组织研究提示炎症区域与 Tau 病理区域重叠。",
  },
  {
    evidence_id: "ev_003",
    claim: "部分研究认为炎症反应可能是病理结果而非原因。",
    source_literature_id: "lit_001",
    evidence_type: "methodological_uncertainty",
    support_direction: "oppose" as const,
    related_concepts: ["神经炎症", "因果推断"],
    strength_score: 0.62,
    summary: "横断面研究无法区分炎症在 Tau 扩散前后的位置。",
  },
];

export const knowledgeGaps = [
  {
    gap_id: "gap_001",
    description: "神经炎症在 Tau 病理扩散中的因果作用仍不明确。",
    related_concepts: ["神经炎症", "Tau蛋白异常"],
    importance_score: 0.86,
  },
  {
    gap_id: "gap_002",
    description: "中国人群纵向数据中的炎症标志物证据不足。",
    related_concepts: ["人群适用性", "纵向队列"],
    importance_score: 0.78,
  },
];

export const hypothesisCards = [
  {
    hypothesis_id: "hyp_001",
    statement: "神经炎症可能通过促进 Tau 病理扩散，加速阿尔茨海默病认知功能下降。",
    rationale:
      "现有证据显示神经炎症、Tau 病理和认知下降均有关联，但神经炎症是否作为中介机制仍存在研究空白。",
    based_on_evidence_ids: ["ev_001", "ev_002"],
    related_gap_ids: ["gap_001"],
    target_variables: ["神经炎症", "Tau蛋白异常", "认知下降"],
    expected_observation:
      "若假设成立，炎症标志物升高应先于或伴随 Tau 病理扩散，并预测认知下降速度。",
    validation_idea: "可通过纵向队列数据、公开影像数据库和结构方程模型验证炎症与 Tau 扩散之间的关系。",
    initial_scores: {
      novelty: 0.72,
      testability: 0.86,
      relevance: 0.91,
      risk: 0.38,
    },
  },
  {
    hypothesis_id: "hyp_002",
    statement: "Aβ 沉积可能为早期启动因素，但 Tau 病理才是认知下降的直接近端驱动。",
    rationale: "证据显示 Aβ 和 Tau 均参与 AD 进展，但 Tau 与认知下降更直接相关。",
    based_on_evidence_ids: ["ev_001"],
    related_gap_ids: ["gap_001"],
    target_variables: ["Aβ沉积", "Tau蛋白异常", "认知下降"],
    expected_observation: "Tau 指标应在控制 Aβ 负荷后仍然显著预测认知下降。",
    validation_idea: "使用多变量回归和中介分析比较 Aβ、Tau 与认知下降的解释力。",
    initial_scores: {
      novelty: 0.61,
      testability: 0.89,
      relevance: 0.84,
      risk: 0.32,
    },
  },
  {
    hypothesis_id: "hyp_003",
    statement: "神经炎症更可能是 Tau 病理扩散后的伴随反应，而不是主要因果驱动。",
    rationale: "现有反对证据提示横断面相关性不足以证明炎症处于因果链上游。",
    based_on_evidence_ids: ["ev_003"],
    related_gap_ids: ["gap_001", "gap_002"],
    target_variables: ["Tau蛋白异常", "神经炎症"],
    expected_observation: "如果假设成立，Tau 指标变化应先于炎症标志物变化。",
    validation_idea: "以时间滞后模型检验 Tau 与炎症标志物的先后顺序。",
    initial_scores: {
      novelty: 0.68,
      testability: 0.77,
      relevance: 0.75,
      risk: 0.45,
    },
  },
];

export const evidenceMap = [
  {
    hypothesis_id: "hyp_001",
    supporting_evidence_ids: ["ev_001", "ev_002"],
    opposing_evidence_ids: ["ev_003"],
    uncertain_evidence_ids: [],
    evidence_summary: {
      support: "Tau 与认知下降关系稳定，炎症与 Tau 区域分布存在重叠。",
      oppose: "横断面研究无法证明神经炎症处于 Tau 扩散上游。",
      uncertain: "缺少多时间点、多人群的因果方向验证。",
    },
    evidence_strength_score: 0.76,
    main_limitations: ["因果证据不足", "不同队列炎症指标不完全一致", "中国人群数据不足"],
    needs_more_evidence: true,
    detailed_review: {
      review_id: "REV_001",
      threshold: 7.0,
      verdict: {
        score: 6.8,
        passed: false,
        reason: "证据链存在关键缺口：炎症与 Tau 的因果方向还没有被直接验证。",
        recommendation: "补充纵向数据或将假设修改为条件性机制假设。",
      },
      feedback_for_iteration: {
        back_to: "both",
        specific_suggestions: ["补充中国人群或公开队列数据", "加入时间滞后分析", "补充反对证据"],
      },
    },
  },
  {
    hypothesis_id: "hyp_002",
    supporting_evidence_ids: ["ev_001"],
    opposing_evidence_ids: [],
    uncertain_evidence_ids: ["ev_002"],
    evidence_summary: {
      support: "Tau 指标与认知下降关系较直接。",
      oppose: "暂无直接反对证据。",
      uncertain: "炎症与 Aβ 的交互作用仍未进入模型。",
    },
    evidence_strength_score: 0.71,
    main_limitations: ["机制解释较传统", "创新性略低"],
    needs_more_evidence: false,
    detailed_review: {
      review_id: "REV_002",
      threshold: 7.0,
      verdict: {
        score: 7.2,
        passed: true,
        reason: "证据链可用于形成保守的可验证研究计划。",
        recommendation: "作为基线假设保留，用于与炎症机制假设对照。",
      },
      feedback_for_iteration: {
        back_to: "research_planning",
        specific_suggestions: ["在计划中作为 baseline 假设"],
      },
    },
  },
];

export const researchPlan: ResearchPlan = {
  schema_version: "experiment_planner_output_v1",
  agent_name: "ExperimentPlannerAgent",
  run_id: "task_001",
  round_id: 1,
  status: "success",
  plans: [
    {
      hypothesis_id: "hyp_001",
      status: "success",
      error_message: null,
      plan: {
        problem_statement:
          "验证神经炎症是否通过促进 Tau 病理扩散，加速阿尔茨海默病患者的认知功能下降。",
        rationale: {
          text: "若炎症标志物能预测后续 Tau 扩散并进一步预测认知下降，则可支持神经炎症位于 Tau 扩散上游或并行放大环节。",
          logic_chain: [
            {
              step: 1,
              claim: "Tau 病理负荷与认知下降程度存在较强相关性。",
              evidence_ids: ["ev_001"],
              source_ids: ["lit_002"],
            },
            {
              step: 2,
              claim: "神经炎症标志物与 Tau 病理空间分布存在重叠。",
              evidence_ids: ["ev_002"],
              source_ids: ["lit_001"],
            },
            {
              step: 3,
              claim: "仍需通过纵向模型区分炎症是原因还是结果。",
              evidence_ids: ["ev_003"],
              source_ids: ["lit_001"],
            },
          ],
        },
        technical_details: {
          required_methods: ["纵向队列分析", "时间滞后模型", "中介效应分析", "敏感性分析"],
          candidate_models_or_algorithms: ["mixed-effects model", "cross-lagged panel model", "Bayesian mediation"],
          statistical_tests: ["Wald test", "likelihood ratio test", "bootstrap mediation CI"],
          software_stack: ["Python", "pandas", "statsmodels", "PyMC", "MNE/PET processing placeholder"],
        },
        datasets: {
          source: [
            {
              dataset_id: "ds_001",
              name: "ADNI longitudinal biomarker cohort",
              usage: "验证炎症、Tau、认知评分的纵向关系。",
              required_fields: ["timepoint", "inflammation_marker", "tau_pet", "cognitive_score", "age", "sex"],
              access_status: "available",
            },
          ],
          target: [
            {
              name: "本项目验证数据表",
              description: "按受试者和时间点整理的多模态纵向表。",
              fields: ["subject_id", "visit_month", "tau_index", "inflammation_index", "mmse", "covariates"],
            },
          ],
        },
        paper_title: "Testing Neuroinflammation-Mediated Tau Propagation in Alzheimer's Disease",
        paper_abstract:
          "本研究计划使用纵向队列和时间滞后模型，检验神经炎症是否促进 Tau 病理扩散并加速认知下降，同时通过反对证据和敏感性分析控制反向因果解释。",
        methods: {
          overall_design: "基于公开纵向数据的机制假设验证研究。",
          steps: [
            {
              step_id: "step_001",
              name: "数据清洗与变量构造",
              description: "统一受试者时间点、炎症指标、Tau 指标和认知评分。",
              input: ["raw cohort table", "biomarker files"],
              output: ["analysis_ready_table"],
            },
            {
              step_id: "step_002",
              name: "主效应与中介模型",
              description: "检验炎症指标对 Tau 扩散和认知下降的预测关系。",
              input: ["analysis_ready_table"],
              output: ["effect estimates", "confidence intervals"],
            },
            {
              step_id: "step_003",
              name: "反向因果与敏感性分析",
              description: "检验 Tau 是否先于炎症变化，并比较不同滞后窗口。",
              input: ["longitudinal features"],
              output: ["falsification report"],
            },
          ],
        },
        experiments: {
          main_experiment: {
            objective: "检验炎症指标是否预测后续 Tau 扩散和认知下降。",
            independent_variables: ["inflammation_index"],
            dependent_variables: ["tau_index_delta", "cognitive_score_delta"],
            control_variables: ["age", "sex", "baseline_Aβ", "education", "APOE4"],
          },
          baselines: [
            {
              name: "Tau-only baseline",
              description: "仅使用 Tau 指标预测认知下降。",
            },
            {
              name: "Aβ + Tau baseline",
              description: "使用 Aβ 与 Tau 共同预测认知下降。",
            },
          ],
          metrics: [
            {
              name: "ΔR2",
              description: "加入炎症指标后模型解释力的提升。",
            },
            {
              name: "mediation_effect",
              description: "炎症通过 Tau 影响认知下降的中介效应大小。",
            },
          ],
          procedure: [
            "构造纵向分析表。",
            "训练基线模型与炎症增强模型。",
            "进行中介效应和时间滞后检验。",
            "输出支持、反对和不确定结论。",
          ],
          ablation_or_sensitivity_analysis: ["更换炎症指标", "改变滞后窗口", "剔除高风险混杂受试者"],
        },
        results: {
          result_type: "expected_or_feasibility_result",
          expected_findings: [
            "炎症指标可能提升 Tau 扩散预测能力。",
            "若时间滞后方向不成立，则假设需要退化为相关性机制假设。",
          ],
          feasibility_check: "公开队列可提供基础字段，但炎症指标的可用性需要进一步确认。",
          falsification_criteria: [
            "炎症指标无法预测后续 Tau 变化。",
            "Tau 变化显著早于炎症变化。",
            "控制 Aβ 与人口学变量后效应消失。",
          ],
        },
        references: [
          {
            source_id: "lit_001",
            title: literatureCards[0].title,
            authors: literatureCards[0].authors,
            year: literatureCards[0].year,
            doi: literatureCards[0].doi,
            url: literatureCards[0].url,
            used_for: ["rationale", "limitations", "falsification_tests"],
          },
          {
            source_id: "lit_002",
            title: literatureCards[1].title,
            authors: literatureCards[1].authors,
            year: literatureCards[1].year,
            doi: literatureCards[1].doi,
            url: literatureCards[1].url,
            used_for: ["datasets", "methods", "experiments"],
          },
        ],
        feedback_tasks: [
          {
            task_id: "fb_task_001",
            task_type: "literature_supplement",
            priority: "high",
            objective: "补充中国人群神经炎症与 Tau 关系的证据。",
            input_requirements: ["question_card", "search_keywords.en", "knowledge_gaps"],
            expected_output: "新增 evidence_cards，并重新计算 evidence_strength_score。",
          },
          {
            task_id: "fb_task_002",
            task_type: "public_dataset_analysis",
            priority: "medium",
            objective: "检查公开数据中炎症指标字段可用性。",
            input_requirements: ["dataset metadata", "required_fields"],
            expected_output: "数据可用性报告和缺失字段列表。",
          },
        ],
        limitations: ["当前为 demo mock 数据", "文献 URL 需由真实知识整合 Agent 替换", "尚未执行真实统计分析"],
      },
    },
  ],
};

export const finalReview: FinalReview = {
  passed: true,
  overall_score: 0.82,
  strengths: [
    "研究问题被转化为可验证机制假设。",
    "候选假设可以追溯到 evidence_ids 和 gap_ids。",
    "研究计划包含变量、数据、方法、指标和失败判据。",
    "反馈任务能驱动下一轮证据补充和计划修订。",
  ],
  weaknesses: ["因果证据仍需真实纵向数据验证。", "当前 demo 文献为占位引用，后续需由真实 Agent 替换。"],
  revision_required: false,
};

export const feedbackEvents = [
  {
    feedback_id: "fb_001",
    round_id: 1,
    feedback_type: "human_feedback",
    target: {
      stage: "evidence_mapping" as const,
      hypothesis_id: "hyp_001",
    },
    input_summary: "人工审核指出：不要把神经炎症直接写成强因果结论。",
    result_summary: "研究计划已加入时间滞后模型、反向因果检验和失败判据。",
    score_delta: {
      evidence_strength: -0.04,
      testability: 0.08,
      feasibility: 0.04,
    },
    controller_action: "refine_hypothesis",
    revision_suggestion: "将假设表述为条件性机制假设，并补充反对证据。",
  },
];

export function createInitialContext(mode: RunMode = "hybrid"): TaskContext {
  return {
    task_id: "task_001",
    mode,
    current_stage: "created",
    iteration: 1,
    user_input: baseUserInput,
    question_card: null,
    literature_cards: [],
    evidence_cards: [],
    knowledge_gaps: [],
    hypothesis_cards: [],
    evidence_map: [],
    research_plan: null,
    final_review: null,
    reviews: [],
    versions: [],
    feedback_events: [],
    model_policy: {
      provider: "dashscope",
      model: "qwen3.7-max",
      reasoning: "high",
      temperature: 0.2,
      max_tokens: 6144,
      timeout_seconds: 120,
      max_retries: 0,
      response_format: "json_object",
      thinking_enabled: false,
    },
  };
}

export function createStageInput(stage: StageId, context: TaskContext): Record<string, unknown> {
  if (stage === "question_understanding") {
    return {
      task_id: context.task_id,
      stage,
      iteration: context.iteration,
      input: {
        user_input: context.user_input,
      },
      output_schema: "question_card.schema.json",
    };
  }

  if (stage === "knowledge_integration") {
    return {
      task_id: context.task_id,
      stage,
      iteration: context.iteration,
      input: {
        question_card: context.question_card ?? questionCard,
        search_policy: {
          max_papers: 20,
          must_verify_sources: true,
          forbidden_actions: ["invent_references", "invent_dataset_url"],
        },
      },
      output_schema: "knowledge_integration.schema.json",
    };
  }

  if (stage === "hypothesis_generation") {
    return {
      task_id: context.task_id,
      stage,
      iteration: context.iteration,
      input: {
        question_card: context.question_card ?? questionCard,
        evidence_cards: context.evidence_cards.length ? context.evidence_cards : evidenceCards,
        knowledge_gaps: context.knowledge_gaps.length ? context.knowledge_gaps : knowledgeGaps,
      },
      output_schema: "hypothesis_cards.schema.json",
    };
  }

  if (stage === "evidence_mapping") {
    return {
      task_id: context.task_id,
      stage,
      iteration: context.iteration,
      input: {
        hypothesis_cards: context.hypothesis_cards.length ? context.hypothesis_cards : hypothesisCards,
        evidence_cards: context.evidence_cards.length ? context.evidence_cards : evidenceCards,
        literature_cards: context.literature_cards.length ? context.literature_cards : literatureCards,
      },
      output_schema: "evidence_map.schema.json",
    };
  }

  if (stage === "research_planning") {
    return {
      task_id: context.task_id,
      stage,
      iteration: context.iteration,
      input: {
        question_card: context.question_card ?? questionCard,
        hypothesis_cards: context.hypothesis_cards.length ? context.hypothesis_cards : hypothesisCards,
        evidence_map: context.evidence_map.length ? context.evidence_map : evidenceMap,
        knowledge_gaps: context.knowledge_gaps.length ? context.knowledge_gaps : knowledgeGaps,
        user_constraints: context.user_input.user_constraints,
      },
      output_schema: "research_plan.schema.json",
    };
  }

  return {
    task_id: context.task_id,
    stage,
    iteration: context.iteration,
    input: {
      task_context: {
        question_card: context.question_card ?? questionCard,
        evidence_cards: context.evidence_cards.length ? context.evidence_cards : evidenceCards,
        hypothesis_cards: context.hypothesis_cards.length ? context.hypothesis_cards : hypothesisCards,
        evidence_map: context.evidence_map.length ? context.evidence_map : evidenceMap,
        research_plan: context.research_plan ?? researchPlan,
      },
    },
    output_schema: "final_review.schema.json",
  };
}

function payloadForStage(stage: StageId): Record<string, unknown> {
  const payloads: Record<StageId, Record<string, unknown>> = {
    question_understanding: { question_card: questionCard },
    knowledge_integration: {
      literature_cards: literatureCards,
      evidence_cards: evidenceCards,
      knowledge_gaps: knowledgeGaps,
    },
    hypothesis_generation: { hypothesis_cards: hypothesisCards },
    evidence_mapping: { evidence_map: evidenceMap },
    research_planning: { research_plan: researchPlan },
    final_review: { final_review: finalReview },
  };
  return payloads[stage];
}

export function createAgentResponse(stage: StageId, taskId = "task_001"): AgentResponse {
  const scoreByStage: Record<StageId, number> = {
    question_understanding: 0.88,
    knowledge_integration: 0.83,
    hypothesis_generation: 0.86,
    evidence_mapping: 0.79,
    research_planning: 0.84,
    final_review: 0.82,
  };

  return {
    metadata: {
      task_id: taskId,
      agent_id: `${stage}_agent`,
      stage,
      iteration: 1,
      status: "success",
    },
    payload: payloadForStage(stage),
    self_review: {
      passed: true,
      overall_score: scoreByStage[stage],
      threshold: 0.75,
      dimension_scores: {
        schema_readiness: 0.94,
        evidence_traceability: stage === "evidence_mapping" ? 0.78 : 0.86,
        downstream_usefulness: 0.88,
      },
      issues: stage === "evidence_mapping" ? ["hyp_001 因果方向仍需补充纵向证据"] : [],
      suggestions:
        stage === "research_planning"
          ? ["后续接入真实公开数据分析 Runner", "将 demo 文献替换成可验证 DOI/URL"]
          : [],
    },
  };
}

export function createReviewRecord(stage: StageId, decision: ReviewRecord["decision"] = "accept"): ReviewRecord {
  const traceScore = stage === "evidence_mapping" ? 0.8 : 0.9;
  const overall = decision === "human_review" ? 0.78 : stage === "evidence_mapping" ? 0.82 : 0.9;

  return {
    review_id: `review_${stage}`,
    task_id: "task_001",
    stage,
    decision,
    comment:
      decision === "human_review"
        ? "Hybrid 模式命中人工审核门，需要确认方向和证据风险后继续。"
        : "格式、必填字段、自评分和下游可用性均通过。",
    score: {
      schema_validity: 1,
      required_fields: 0.96,
      downstream_readiness: 0.91,
      evidence_traceability: traceScore,
      iteration_value: stage === "final_review" ? 0.9 : 0.82,
    },
    overall_score: overall,
    operator: decision === "human_review" ? "human" : "system",
    created_at: new Date().toISOString(),
  };
}

export function createInitialStages(context: TaskContext): StageRun[] {
  return stageOrder.map((stage) => ({
    id: stage,
    label: stageMeta[stage].label,
    agent: stageMeta[stage].agent,
    description: stageMeta[stage].description,
    status: "queued",
    duration: "0.0s",
    allowedWrites: stageMeta[stage].allowedWrites,
    input: createStageInput(stage, context),
    output: null,
    review: null,
  }));
}

export function mergeStagePayload(context: TaskContext, stage: StageId, response: AgentResponse): TaskContext {
  const payload = response.payload as Record<string, unknown>;
  const next: TaskContext = {
    ...context,
    current_stage: stage === "final_review" ? "completed" : stageOrder[stageOrder.indexOf(stage) + 1],
  };

  if (stage === "question_understanding") {
    next.question_card = payload.question_card as TaskContext["question_card"];
  }
  if (stage === "knowledge_integration") {
    next.literature_cards = payload.literature_cards as TaskContext["literature_cards"];
    next.evidence_cards = payload.evidence_cards as TaskContext["evidence_cards"];
    next.knowledge_gaps = payload.knowledge_gaps as TaskContext["knowledge_gaps"];
  }
  if (stage === "hypothesis_generation") {
    next.hypothesis_cards = payload.hypothesis_cards as TaskContext["hypothesis_cards"];
  }
  if (stage === "evidence_mapping") {
    next.evidence_map = payload.evidence_map as TaskContext["evidence_map"];
  }
  if (stage === "research_planning") {
    next.research_plan = payload.research_plan as TaskContext["research_plan"];
    next.feedback_events = feedbackEvents;
  }
  if (stage === "final_review") {
    next.final_review = payload.final_review as TaskContext["final_review"];
  }

  return next;
}

export function createVersion(stage: StageId, index: number): VersionRecord {
  const versionId = `v${String(index + 1).padStart(3, "0")}`;
  const fields = stageMeta[stage].allowedWrites;
  return {
    version_id: versionId,
    iteration: 1,
    stage,
    trigger: stage === "final_review" ? "final_review_passed" : "validation_passed",
    changed_fields: fields,
    summary: `${stageMeta[stage].label} 完成，写入 ${fields.join(", ")}。`,
    artifact_path: `versions/context_${versionId}.json`,
    created_at: new Date().toISOString(),
  };
}

export const artifactItems: ArtifactItem[] = [
  {
    artifact_id: "manifest",
    kind: "manifest",
    path: "artifacts/tasks/task_001/manifest.json",
    status: "ready",
    description: "任务元数据、执行模式、当前阶段和 artifact 索引。",
  },
  {
    artifact_id: "context_latest",
    kind: "context",
    path: "artifacts/tasks/task_001/context/task_context.latest.json",
    status: "ready",
    description: "总控维护的最新 task_context。",
  },
  ...stageOrder.flatMap((stage) => [
    {
      artifact_id: `${stage}_input`,
      kind: "input" as const,
      path: `artifacts/tasks/task_001/stages/${stage}/i001.input.json`,
      stage,
      status: "ready" as const,
      description: "总控裁剪后传给 Agent 的输入切片。",
    },
    {
      artifact_id: `${stage}_output`,
      kind: "output" as const,
      path: `artifacts/tasks/task_001/stages/${stage}/latest.output.json`,
      stage,
      status: "ready" as const,
      description: "Agent 原始统一响应，包含 metadata、payload、self_review。",
    },
  ]),
  {
    artifact_id: "trace",
    kind: "event",
    path: "artifacts/tasks/task_001/events/trace.jsonl",
    status: "ready",
    description: "任务执行事件流，用于前端 Event Console。",
  },
  {
    artifact_id: "report",
    kind: "report",
    path: "artifacts/tasks/task_001/reports/final_report.md",
    status: "planned",
    description: "后续由后端导出的技术报告草稿。",
  },
  {
    artifact_id: "bundle",
    kind: "export",
    path: "artifacts/tasks/task_001/exports/submission_bundle.zip",
    status: "ready",
    description: "比赛提交包，占位等待后端 Export Service。",
  },
];

export const apiSpecs: ApiSpec[] = [
  {
    method: "GET",
    path: "/api/tasks",
    capability: "tasks",
    owner: "TaskContext Manager",
    status: "ready",
    writes: "none",
    description: "恢复未归档项目列表。",
  },
  {
    method: "POST",
    path: "/api/tasks",
    capability: "tasks",
    owner: "TaskContext Manager",
    status: "ready",
    writes: "manifest.json, task_context.latest.json",
    description: "创建科研任务并初始化 task_context。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/archive",
    capability: "task_archive",
    owner: "TaskContext Manager",
    status: "ready",
    writes: "manifest.json",
    description: "归档或恢复项目。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/attachments",
    capability: "attachments",
    owner: "Artifact Service",
    status: "ready",
    writes: "none",
    description: "列出任务已持久化的附件。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/attachments",
    capability: "attachments",
    owner: "Artifact Service",
    status: "ready",
    writes: "attachments/, task_context.latest.json",
    description: "上传文本背景材料并注入任务上下文。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/start",
    capability: "task_start",
    owner: "Workflow Orchestrator",
    status: "ready",
    writes: "events/trace.jsonl",
    description: "启动总控状态机，依次调度 Agent。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/context",
    capability: "tasks",
    owner: "TaskContext Manager",
    status: "ready",
    writes: "none",
    description: "读取完整 task_context，供前端 Debug View 展示。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/stages/{stage}",
    capability: "stage_run",
    owner: "Artifact Service",
    status: "ready",
    writes: "none",
    description: "读取阶段 input/output/review。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/reviews",
    capability: "reviews",
    owner: "Review Gate",
    status: "ready",
    writes: "reviews/{stage}.review.json",
    description: "提交人工审核决策：通过、重试、回退或终止。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/events/stream",
    capability: "events",
    owner: "Event & Trace Logger",
    status: "ready",
    writes: "none",
    description: "通过 SSE 推送任务事件与心跳。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/feedback",
    capability: "feedback",
    owner: "Iteration Controller",
    status: "ready",
    writes: "feedback_events, versions/",
    description: "触发反馈任务并驱动下一轮修订。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/versions",
    capability: "versions",
    owner: "Version Manager",
    status: "ready",
    writes: "none",
    description: "读取任务版本快照。",
  },
  {
    method: "GET",
    path: "/api/tasks/{task_id}/artifacts",
    capability: "artifacts",
    owner: "Artifact Service",
    status: "ready",
    writes: "none",
    description: "列出任务隔离目录中的产物。",
  },
  {
    method: "POST",
    path: "/api/tasks/{task_id}/export",
    capability: "export",
    owner: "Export Service",
    status: "ready",
    writes: "exports/submission_bundle.zip",
    description: "导出比赛提交包。",
  },
];

export const seedEvents: EventLog[] = [
  {
    event_id: "evt_seed_001",
    task_id: "task_001",
    type: "task_created",
    message: "task_context 已初始化，等待启动总控流程。",
    created_at: new Date().toISOString(),
  },
];
