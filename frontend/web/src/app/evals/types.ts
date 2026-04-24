export type EvalFlowMeta = {
  flow_id: string;
  label: string;
  description: string;
  case_count: number;
  cases: Array<{
    case_id: string;
    title: string;
    description?: string | null;
  }>;
};

export type EvalRunResult = {
  flow: {
    flow_id: string;
    label: string;
    description: string;
    case_count: number;
  };
  llm_model?: string | null;
  timeout_seconds?: number | null;
  repetitions: number;
  discard_first_attempt: boolean;
  warmup?: {
    attempted: boolean;
    performed: boolean;
    model?: string | null;
    duration_ms?: number;
    error?: string;
  };
  summary: {
    total_attempts: number;
    measured_attempts: number;
    discarded_attempts: number;
    passed_attempts: number;
    pass_rate: number;
    avg_duration_ms: number;
    total_duration_ms: number;
  };
  cases: Array<{
    case_id: string;
    title: string;
    description?: string | null;
    input: Record<string, unknown>;
    expected: Record<string, unknown>;
    metrics: {
      attempts: number;
      total_attempts: number;
      discarded_attempts: number;
      passed_attempts: number;
      pass_rate: number;
      avg_duration_ms: number;
      variant_count: number;
    };
    attempts: Array<{
      attempt: number;
      duration_ms: number;
      passed: boolean;
      discarded: boolean;
      notes: string[];
      summary: Record<string, unknown>;
      output: Record<string, unknown>;
    }>;
  }>;
};

export type EvalRunJob = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  flow_id: string;
  flow_label?: string | null;
  llm_model?: string | null;
  repetitions: number;
  discard_first_attempt: boolean;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  progress?: {
    current_case?: number;
    total_cases?: number;
    current_attempt?: number;
    total_attempts?: number;
    current_case_id?: string | null;
    current_case_title?: string | null;
    attempt_in_case?: number;
    repetitions?: number;
    status?: string;
  } | null;
  result?: EvalRunResult | null;
};
