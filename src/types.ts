export type SurfaceMode = "web" | "app";

export type RunMode = "auto" | "manual" | "hybrid";

export type StageId =
  | "question_understanding"
  | "knowledge_integration"
  | "hypothesis_generation"
  | "evidence_mapping"
  | "research_planning"
  | "final_review";

export type StageStatus =
  | "queued"
  | "running"
  | "validating"
  | "human_review"
  | "passed"
  | "failed"
  | "retrying";

export type ViewId = "workbench" | "research" | "artifacts" | "api" | "submission";

export interface UserInput {
  original_question: string;
  user_constraints: {
    language: string;
    domain_preference: string;
    max_hypotheses: number;
    output_detail_level: "brief" | "standard" | "detailed";
  };
}

export interface QuestionCard {
  question_id: string;
  original_question: string;
  core_question: string;
  question_type: string;
  domain: string[];
  research_object: {
    name: string;
    type: string;
    aliases: string[];
  };
  key_concepts: Array<{
    name: string;
    normalized_name: string;
    category: string;
  }>;
  key_variables: Array<{
    name: string;
    type: string;
    role: string;
  }>;
  sub_questions: Array<{
    sub_question_id: string;
    content: string;
  }>;
  research_scope: {
    included: string[];
    excluded: string[];
  };
  search_keywords: {
    zh: string[];
    en: string[];
  };
}

export interface LiteratureCard {
  literature_id: string;
  title: string;
  authors: string[];
  year: number;
  source: string;
  doi: string;
  url: string;
  literature_type: string;
  relevance_score: number;
  main_findings: string[];
  related_concepts: string[];
}

export interface EvidenceCard {
  evidence_id: string;
  claim: string;
  source_literature_id: string;
  evidence_type: string;
  support_direction: "support" | "oppose" | "uncertain";
  related_concepts: string[];
  strength_score: number;
  summary: string;
}

export interface KnowledgeGap {
  gap_id: string;
  description: string;
  related_concepts: string[];
  importance_score: number;
}

export interface HypothesisCard {
  hypothesis_id: string;
  statement: string;
  rationale: string;
  based_on_evidence_ids: string[];
  related_gap_ids: string[];
  target_variables: string[];
  expected_observation: string;
  validation_idea: string;
  initial_scores: {
    novelty: number;
    testability: number;
    relevance: number;
    risk: number;
  };
}

export interface EvidenceMapItem {
  hypothesis_id: string;
  supporting_evidence_ids: string[];
  opposing_evidence_ids: string[];
  uncertain_evidence_ids: string[];
  evidence_summary: {
    support: string;
    oppose: string;
    uncertain: string;
  };
  evidence_strength_score: number;
  main_limitations: string[];
  needs_more_evidence: boolean;
  detailed_review: {
    review_id: string;
    threshold: number;
    verdict: {
      score: number;
      passed: boolean;
      reason: string;
      recommendation: string;
    };
    feedback_for_iteration: {
      back_to: string;
      specific_suggestions: string[];
    };
  };
}

export interface ResearchPlan {
  schema_version: string;
  agent_name: string;
  run_id: string;
  round_id: number;
  status: "success" | "partial_success" | "failed";
  plans: Array<{
    hypothesis_id: string;
    status: "success" | "failed";
    error_message: string | null;
    plan: {
      problem_statement: string;
      rationale: {
        text: string;
        logic_chain: Array<{
          step: number;
          claim: string;
          evidence_ids: string[];
          source_ids: string[];
        }>;
      };
      technical_details: {
        required_methods: string[];
        candidate_models_or_algorithms: string[];
        statistical_tests: string[];
        software_stack: string[];
      };
      datasets: {
        source: Array<{
          dataset_id: string;
          name: string;
          usage: string;
          required_fields: string[];
          access_status: string;
        }>;
        target: Array<{
          name: string;
          description: string;
          fields: string[];
        }>;
      };
      paper_title: string;
      paper_abstract: string;
      methods: {
        overall_design: string;
        steps: Array<{
          step_id: string;
          name: string;
          description: string;
          input: string[];
          output: string[];
        }>;
      };
      experiments: {
        main_experiment: {
          objective: string;
          independent_variables: string[];
          dependent_variables: string[];
          control_variables: string[];
        };
        baselines: Array<{
          name: string;
          description: string;
        }>;
        metrics: Array<{
          name: string;
          description: string;
        }>;
        procedure: string[];
        ablation_or_sensitivity_analysis: string[];
      };
      results: {
        result_type: string;
        expected_findings: string[];
        feasibility_check: string;
        falsification_criteria: string[];
      };
      references: Array<{
        source_id: string;
        title: string;
        authors: string[];
        year: number;
        doi: string;
        url: string;
        used_for: string[];
      }>;
      feedback_tasks: Array<{
        task_id: string;
        task_type: string;
        priority: "high" | "medium" | "low";
        objective: string;
        input_requirements: string[];
        expected_output: string;
      }>;
      limitations: string[];
    };
  }>;
}

export interface FinalReview {
  passed: boolean;
  overall_score: number;
  strengths: string[];
  weaknesses: string[];
  revision_required: boolean;
}

export interface VersionRecord {
  version_id: string;
  iteration: number;
  stage: StageId | "feedback_revision";
  trigger: string;
  changed_fields: string[];
  summary: string;
  artifact_path: string;
  created_at: string;
}

export interface FeedbackEvent {
  feedback_id: string;
  round_id: number;
  feedback_type: string;
  target: {
    stage: StageId;
    hypothesis_id: string;
  };
  input_summary: string;
  result_summary: string;
  score_delta: {
    evidence_strength: number;
    testability: number;
    feasibility: number;
  };
  controller_action: string;
  revision_suggestion: string;
}

export interface TaskContext {
  task_id: string;
  mode: RunMode;
  current_stage: StageId | "created" | "completed" | "human_review";
  iteration: number;
  user_input: UserInput;
  question_card: QuestionCard | null;
  literature_cards: LiteratureCard[];
  evidence_cards: EvidenceCard[];
  knowledge_gaps: KnowledgeGap[];
  hypothesis_cards: HypothesisCard[];
  evidence_map: EvidenceMapItem[];
  research_plan: ResearchPlan | null;
  final_review: FinalReview | null;
  reviews: ReviewRecord[];
  versions: VersionRecord[];
  feedback_events: FeedbackEvent[];
}

export interface AgentResponse<TPayload = Record<string, unknown>> {
  metadata: {
    task_id: string;
    agent_id: string;
    stage: StageId;
    iteration: number;
    status: "success" | "partial_success" | "failed";
  };
  payload: TPayload;
  self_review: {
    passed: boolean;
    overall_score: number;
    threshold: number;
    dimension_scores: Record<string, number>;
    issues: string[];
    suggestions: string[];
  };
}

export interface ReviewRecord {
  review_id: string;
  task_id: string;
  stage: StageId;
  decision: "accept" | "human_review" | "retry" | "rollback" | "fail";
  comment: string;
  score: {
    schema_validity: number;
    required_fields: number;
    downstream_readiness: number;
    evidence_traceability: number;
    iteration_value: number;
  };
  overall_score: number;
  operator: "system" | "human";
  created_at: string;
}

export interface StageRun {
  id: StageId;
  label: string;
  agent: string;
  description: string;
  status: StageStatus;
  duration: string;
  allowedWrites: string[];
  input: Record<string, unknown>;
  output: AgentResponse | null;
  review: ReviewRecord | null;
}

export interface EventLog {
  event_id: string;
  task_id: string;
  type: string;
  stage?: StageId | "final_review" | "feedback_revision";
  message: string;
  created_at: string;
}

export interface ArtifactItem {
  artifact_id: string;
  kind: "manifest" | "context" | "input" | "output" | "review" | "version" | "event" | "report" | "export";
  path: string;
  stage?: StageId | "final_review";
  status: "ready" | "planned" | "mocked";
  description: string;
}

export interface ApiSpec {
  method: "GET" | "POST";
  path: string;
  owner: string;
  status: "mocked" | "planned";
  writes: string;
  description: string;
}
