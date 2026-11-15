export type HealthState = "ok" | "warn" | "down" | "absent";
export interface ComponentStatus {
  status: HealthState;
  detail: string;
}
export interface Lane {
  model: string;
  capacity: number;
  reserved?: number;
  running: number;
  waiting: number;
  oldest_wait_s: number;
  priority_waiting?: number;
}
export interface Concurrency {
  global_limit: number;
  active: number;
  waiting: number;
  max_queue: number;
  switch_after_s: number;
  turn: { model: string; age_s: number; yielding: boolean } | null;
  lanes: Lane[];
  stats: { switches?: number; expired?: number; rejected?: number };
}
export interface Status {
  ts: string;
  components: Record<string, ComponentStatus>;
  gateway: {
    uptime_s: number;
    concurrency: Concurrency;
    pull_jobs_running: number;
  };
}
export interface Info {
  demo_mode?: boolean;
  public_base_url: string | null;
  client_guide_url: string | null;
  defaults: {
    rpm: number;
    max_concurrent: number;
    global_concurrency: number;
    max_tokens_cap: number;
  };
  models: string[];
}
export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  rpm_limit: number | null;
  max_concurrent: number | null;
  rpm_limit_effective: number;
  max_concurrent_effective: number;
  daily_token_limit: number | null;
  monthly_token_limit: number | null;
  expires_at: string | null;
  allowed_models: string[] | null;
  priority: number;
  log_prompts: number;
  expired: boolean;
  usage: { day_tokens: number; month_tokens: number };
}
export interface KeyList {
  data: ApiKey[];
  quota_resets: { day: string; month: string };
}
export interface CreatedKey extends ApiKey {
  key: string;
  note: string;
}
export interface UsageRow {
  key?: number;
  key_name?: string;
  model?: string;
  day?: string;
  hour?: string;
  requests: number;
  errors: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_ms: number;
}
export interface Usage {
  data: UsageRow[];
  since: string;
  until: string | null;
  group_by: string;
}
export interface RequestRow {
  id: number;
  ts: string;
  key_id: number;
  model: string | null;
  endpoint: string;
  status_code: number;
  total_tokens: number;
  latency_ms: number;
  client_ip: string | null;
  stream: boolean;
}
export interface PromptRow extends RequestRow {
  key_name: string | null;
  end_user: string | null;
  prompt_preview: string | null;
  response_preview: string | null;
}
export interface PromptList {
  data: PromptRow[];
  next: string | null;
  enabled: boolean;
  retention_days: number;
}
export interface PromptDetail extends PromptRow {
  request_id: string | null;
  prompt: { role: string; content: string }[];
  response: string | null;
}
export interface Model {
  name: string;
  installed: boolean;
  enabled: boolean;
  size_bytes: number | null;
  parameter_size: string | null;
  quantization: string | null;
}
export interface PullJob {
  id: string;
  model: string;
  status: string;
  percent?: number | null;
  error?: string | null;
}
