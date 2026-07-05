// Raw response shapes from the iPracticom Sweeper agent API.
// These mirror the backend JSON (verified against dashboard.py / agent_api.py)
// and are kept separate from the UI types in ../types.ts. Adapters in
// adapters.ts translate these into the UI's Machine/Alert shapes.

export interface RawFleetHost {
  name: string;
  kind: 'local' | 'connector';
  status: 'ok' | 'warn' | 'crit' | 'error' | 'unknown';
  // local host
  last_seen?: string | null;
  defcon?: number | null;
  problems_found?: number | null;
  // connector host
  instance_id?: string;
  region?: string;
  enabled?: boolean;
  tags?: Record<string, string>;
  last_collected_at?: number | null;
  last_error?: string | null;
}

export interface RawFleet {
  count: number;
  hosts: RawFleetHost[];
}

export interface RawModule {
  status?: string;
  values?: Record<string, unknown>;
}

export interface RawSnapshot {
  server?: string;
  defcon?: number;
  defcon_label?: string;
  problems_found?: number;
  monitor_overall?: string;
  diagnosis?: {
    summary?: string;
    modules?: Record<string, RawModule>;
    problems?: unknown[];
  };
  modules?: Record<string, RawModule>;
  [k: string]: unknown;
}

// /v6/alerts item — NOTE: no id, no message, no priority (synthesized in adapter).
export interface RawV6Alert {
  ts: string;
  module: string;
  status: string; // crit | warn | yellow | red | orange
  host: string;
  tab: string; // network | performance | security | system | other
}

export interface RawV6AlertsResponse {
  alerts: RawV6Alert[];
  tab: string;
  count: number;
  crit_count: number;
  ts: string;
}

export interface RawConnector {
  name: string;
  instance_id: string;
  region: string;
  tags: Record<string, string>;
  enabled: boolean;
  created_at: number;
  last_collected_at: number | null;
  last_error: string | null;
}

// GET /api/modules — the module catalog (monitors/repairs/runbooks the agent
// can run). Mirrors _module_info_to_dict in agent_api.py.
export interface RawModuleParam {
  name: string;
  type: 'int' | 'float' | 'str' | 'list' | 'bool';
  default: unknown;
}

export interface RawModuleInfo {
  kind: 'monitor' | 'repair' | 'runbook';
  name: string;
  title_en: string;
  title_he: string;
  description: string;
  tags: string[];
  risk: 'low' | 'medium' | 'high';
  params: RawModuleParam[];
  catalog_only: boolean; // true = no code backing; cannot be enabled
  // Only present on sub-checks from /api/checks/<monitor>:
  parent?: string; // the bundle monitor this check belongs to (e.g. "freeswitch")
  liveness?: boolean; // core heartbeat check (FS-01..05)
}

// GET/PUT /api/hosts/<name> — per-host module configuration.
// Each entry flattens its module-specific settings at the top level, alongside
// the reserved fields below (mirrors _host_config_to_dict in agent_api.py).
export interface RawMonitorConfig {
  name: string;
  enabled: boolean;
  interval_sec: number;
  [setting: string]: unknown;
}

export interface RawRepairConfig {
  name: string;
  enabled: boolean;
  require_approval: boolean;
  [setting: string]: unknown;
}

export interface RawRunbookConfig {
  name: string;
  enabled: boolean;
  [setting: string]: unknown;
}

export interface RawHostConfig {
  name: string;
  description: string;
  enabled: boolean;
  updated_at?: string;
  monitors: RawMonitorConfig[];
  repairs: RawRepairConfig[];
  runbooks: RawRunbookConfig[];
  suppressions?: Array<{rule: string; until: string | null; reason: string}>;
}

export interface RawProposal {
  id: string;
  action: string;
  kwargs: Record<string, unknown>;
  reason: string;
  problem?: unknown;
  proposed_command: string;
  snapshot_id?: string | null;
  created_at: string;
  created_at_ts?: number;
  status: string;
  server?: string;
}

export interface RawApprovals {
  count: number;
  pending: RawProposal[];
}

export interface RawBlocked {
  key: string;
  action: string;
  server: string;
  reason: string;
  blocked_at: string;
}

export interface RawBlockedList {
  count: number;
  blocked: RawBlocked[];
}

export interface RawHeatmap {
  grid: number[][];
  days: number;
  hours: number;
  source?: string;
}

export interface RawUptimePoint {
  date: string;
  ratio: number;
}

export interface RawUptime {
  points: RawUptimePoint[];
  days: number;
  source?: string;
}

export interface RawV6Logs {
  log: string | null;
  log_path: string | null;
  lines: string[];
  ts: string;
}

export interface RawHistorySample {
  ts: number;
  value: number;
}

export interface RawHistory {
  host: string;
  metric: string;
  hours?: number;
  count?: number;
  samples: RawHistorySample[];
  note?: string;
}

export interface RawPrediction {
  metric?: string;
  current?: number;
  threshold?: number;
  eta_seconds?: number | null;
  will_cross?: boolean;
  [k: string]: unknown;
}

export interface RawPredictions {
  host?: string;
  count?: number;
  predictions: RawPrediction[];
  note?: string;
}

export interface RawNotificationSettings {
  telegram_bot_token_set: boolean;
  telegram_chat_id: string;
  slack_webhook_set: boolean;
}

// GET /api/settings/bots — merged, secret-free view of every notification bot.
// Telegram bots carry {chat_id}; Slack App bots carry {channel, kind:'app'}.
// `legacy: true` marks the single env target (from notifications.env), which is
// read-only apart from delete (which clears the env var).
export interface RawBot {
  id: string;
  name: string;
  platform: 'telegram' | 'slack';
  token_set: boolean;
  legacy: boolean;
  chat_id?: string;
  channel?: string;
  kind?: string;
}

export interface RawBots {
  telegram: RawBot[];
  slack: RawBot[];
}

export interface RawThresholds {
  cpu: Record<string, number>;
  memory: Record<string, number>;
  disk: Record<string, number>;
  editable?: boolean;
}

export interface RawFilterRules {
  rules: Array<{
    id: string;
    name: string;
    pattern: string;
    action: 'alert' | 'ignore' | 'log';
    enabled: boolean;
    recoveryAction?: 'none' | 'restart_service' | 'run_script';
    recoveryScript?: string;
    enforced?: boolean;
  }>;
  enforced: boolean;
  note?: string;
}
