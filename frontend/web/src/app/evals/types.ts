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
