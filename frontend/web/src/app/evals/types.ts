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
  summary: {
    total_attempts: number;
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
      passed_attempts: number;
      pass_rate: number;
      avg_duration_ms: number;
      variant_count: number;
    };
    attempts: Array<{
      attempt: number;
      duration_ms: number;
      passed: boolean;
      notes: string[];
      summary: Record<string, unknown>;
      output: Record<string, unknown>;
    }>;
  }>;
};
