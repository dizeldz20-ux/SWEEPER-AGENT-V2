// Typed wrappers around every agent endpoint the SPA uses.
// Return RAW agent shapes (see agentTypes.ts); adapters translate to UI types.

import {request, url} from './http';
import type {
  RawApprovals,
  RawBlockedList,
  RawBot,
  RawBots,
  RawConnector,
  RawFleet,
  RawFleetHost,
  RawFilterRules,
  RawHeatmap,
  RawHistory,
  RawHostConfig,
  RawModuleInfo,
  RawNotificationSettings,
  RawPredictions,
  RawSnapshot,
  RawThresholds,
  RawUptime,
  RawV6AlertsResponse,
  RawV6Logs,
} from './agentTypes';

// --- Snapshot / fleet --------------------------------------------------------
export const getSnapshot = (signal?: AbortSignal) =>
  request<RawSnapshot>('/api/snapshot', {signal});

// A fresh sweep. With no args: the full agent-wide sweep + auto-repair (legacy).
// The per-machine console passes {host, repair:false} so the manual scan is
// scoped to that host's ENABLED checks and runs diagnose-only (no repairs).
export const runSweep = (opts?: {host?: string; repair?: boolean}) => {
  const form: Record<string, string> = {};
  if (opts?.host) form.host = opts.host;
  if (opts?.repair === false) form.repair = '0';
  return request<RawSnapshot>('/api/run', {method: 'POST', form});
};

export const getFleet = (signal?: AbortSignal) =>
  request<RawFleet>('/api/fleet', {signal});

export const getFleetHost = (host: string, signal?: AbortSignal) =>
  request<RawFleetHost>(`/api/fleet/${encodeURIComponent(host)}`, {signal});

// --- Alerts (v6 live feed) ---------------------------------------------------
export const getAlerts = (tab = 'all', signal?: AbortSignal) =>
  request<RawV6AlertsResponse>(`/v6/alerts?tab=${encodeURIComponent(tab)}`, {signal});

export const resolveAlert = (id: string, note?: string) =>
  request(`/v6/alerts/${encodeURIComponent(id)}/resolve`, {
    method: 'POST',
    form: note ? {note} : {},
  });

export const snoozeAlert = (id: string, durationMin: number) =>
  request(`/v6/alerts/${encodeURIComponent(id)}/snooze`, {
    method: 'POST',
    form: {duration_min: durationMin},
  });

// --- Machine actions (approval-gated) ---------------------------------------
export type MachineAction = 'agent_restart' | 'reboot' | 'ssm_connect';

export const machineAction = (host: string, action: MachineAction) =>
  request(`/v6/machines/${encodeURIComponent(host)}/action`, {
    method: 'POST',
    form: {action},
  });

export const setMaintenance = (host: string, durationMin: number) =>
  request(`/v6/machines/${encodeURIComponent(host)}/maintenance`, {
    method: 'POST',
    form: {duration_min: durationMin},
  });

export const clearMaintenance = (host: string) =>
  request(`/v6/machines/${encodeURIComponent(host)}/maintenance/off`, {method: 'POST'});

// --- Metrics / logs ----------------------------------------------------------
export const getHeatmap = (signal?: AbortSignal) =>
  request<RawHeatmap>('/v6/metrics/events_heatmap', {signal});

export const getUptime30d = (signal?: AbortSignal) =>
  request<RawUptime>('/v6/metrics/uptime_30d', {signal});

export const getV6Logs = (signal?: AbortSignal) =>
  request<RawV6Logs>('/v6/logs', {signal});

export const getHistory = (
  metric: string,
  params: {host?: string; hours?: number; limit?: number} = {},
  signal?: AbortSignal,
) => {
  const q = new URLSearchParams();
  if (params.host) q.set('host', params.host);
  if (params.hours) q.set('hours', String(params.hours));
  if (params.limit) q.set('limit', String(params.limit));
  const qs = q.toString();
  return request<RawHistory>(
    `/api/history/${encodeURIComponent(metric)}${qs ? `?${qs}` : ''}`,
    {signal},
  );
};

// --- Connectors (AWS SSM) ----------------------------------------------------
export const listConnectors = (signal?: AbortSignal) =>
  request<RawConnector[]>('/api/connectors', {signal});

export const createConnector = (body: {
  name: string;
  instance_id: string;
  region?: string;
  tags?: Record<string, string>;
  enabled?: boolean;
  freeswitch_enabled?: boolean;
}) => request<RawConnector>('/api/connectors', {method: 'POST', json: body});

export const updateConnector = (name: string, body: Record<string, unknown>) =>
  request<RawConnector>(`/api/connectors/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    json: body,
  });

export const deleteConnector = (name: string) =>
  request<void>(`/api/connectors/${encodeURIComponent(name)}`, {method: 'DELETE'});

export const testConnector = (name: string) =>
  request<{ok: boolean; snapshot?: unknown; error?: string}>(
    `/api/connectors/${encodeURIComponent(name)}/test`,
    {method: 'POST'},
  );

// --- Agent self-health (self-monitoring) -------------------------------------
// The agent's OWN health: machine resilience + notification-bot connectivity.
export interface RawSelfBot {
  platform: 'telegram' | 'slack';
  id: string;
  name: string;
  status: 'ok' | 'warn' | 'crit' | 'disabled' | 'unknown';
  identity: string | null;
  error: string | null;
  latency_ms: number | null;
}

export interface RawSelfHealth {
  degraded: boolean;
  state_dir_pct: number | null;
  audit_size_bytes: number | null;
  watchdog_restart_count: number;
  uptime_seconds: number;
  self_defcon: number;
  summary: string;
  bot_token_status: string;
  bots: {
    status: 'ok' | 'warn' | 'crit' | 'disabled' | 'unknown';
    defcon: number;
    summary: string;
    counts: Record<string, number>;
    configured: number;
    checked_at: number | null;
    items: RawSelfBot[];
  } | null;
  healthz?: {
    status: string;
    status_code: number | null;
    latency_ms: number | null;
    url: string;
    error: string;
  };
}

export const getSelfHealth = (signal?: AbortSignal) =>
  request<RawSelfHealth>('/api/self-health', {signal});

// --- Module catalog + per-host config ---------------------------------------
export const listModules = (signal?: AbortSignal) =>
  request<RawModuleInfo[]>('/api/modules', {signal});

// Sub-checks of a bundle monitor (currently only "freeswitch" → FS-01..40).
export const listMonitorChecks = (monitor: string, signal?: AbortSignal) =>
  request<RawModuleInfo[]>(`/api/checks/${encodeURIComponent(monitor)}`, {signal});

export const getHostConfig = (name: string, signal?: AbortSignal) =>
  request<RawHostConfig>(`/api/hosts/${encodeURIComponent(name)}`, {signal});

export const saveHostConfig = (name: string, body: Partial<RawHostConfig>) =>
  request<RawHostConfig>(`/api/hosts/${encodeURIComponent(name)}`, {
    method: 'PUT',
    json: body,
  });

// --- Approvals ---------------------------------------------------------------
export const listApprovals = (signal?: AbortSignal) =>
  request<RawApprovals>('/api/approvals', {signal});

export const approveProposal = (id: string) =>
  request(`/api/approvals/${encodeURIComponent(id)}/approve`, {method: 'POST'});

// The API requires a non-empty `reason` (400 otherwise); the dashboard button
// carries a default so a quick reject still succeeds.
export const rejectProposal = (id: string, reason = 'נדחה מהדאשבורד') =>
  request(`/api/approvals/${encodeURIComponent(id)}/reject`, {
    method: 'POST',
    json: {reason},
  });

// Escalation blocks (created when an approved repair fails).
export const listBlocked = (signal?: AbortSignal) =>
  request<RawBlockedList>('/api/approvals/blocked', {signal});

export const unblockKey = (key: string) =>
  request(`/api/approvals/blocked/${encodeURIComponent(key)}`, {method: 'DELETE'});

// --- Predictions / evidence --------------------------------------------------
// All three accept an optional `host` so the per-machine console can scope
// predictions/evidence/logs to one machine; omitting it keeps the legacy
// (local host) behaviour.
export const getPredictions = (host?: string, signal?: AbortSignal) =>
  request<RawPredictions>(
    `/api/predictions${host ? `?host=${encodeURIComponent(host)}` : ''}`,
    {signal},
  );

export const evidenceExportUrl = (
  hours = 24,
  format: 'json' | 'file' = 'json',
  host?: string,
) =>
  url(
    `/api/evidence/export?hours=${hours}&format=${format}${
      host ? `&host=${encodeURIComponent(host)}` : ''
    }`,
  );

export const logDownloadUrl = (name: string, host?: string) =>
  url(
    `/api/logs/download?name=${encodeURIComponent(name)}${
      host ? `&host=${encodeURIComponent(host)}` : ''
    }`,
  );

// --- Settings ----------------------------------------------------------------
export const getNotificationSettings = (signal?: AbortSignal) =>
  request<RawNotificationSettings>('/api/settings/notifications', {signal});

export const updateNotificationSettings = (body: {
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  slack_webhook_url?: string;
}) => request<{ok: boolean; error?: string}>('/api/settings/notifications', {
  method: 'PUT',
  json: body,
});

export const testNotification = (channel: 'telegram' | 'slack') =>
  request<{ok: boolean; message?: string; error?: string}>(
    '/api/settings/notifications/test',
    {method: 'POST', json: {channel}},
  );

// --- Notification bots (multi-bot: Telegram + Slack App) ---------------------
export const listBots = (signal?: AbortSignal) =>
  request<RawBots>('/api/settings/bots', {signal});

export const addBot = (body: {
  platform: 'telegram' | 'slack';
  name?: string;
  bot_token: string;
  chat_id?: string;
  channel?: string;
}) =>
  request<{ok: boolean; bot?: RawBot; error?: string}>('/api/settings/bots', {
    method: 'POST',
    json: body,
  });

export const deleteBot = (id: string) =>
  request<{ok: boolean; error?: string}>(
    `/api/settings/bots/${encodeURIComponent(id)}`,
    {method: 'DELETE'},
  );

export const testBot = (id: string) =>
  request<{ok: boolean; message?: string; error?: string}>(
    `/api/settings/bots/${encodeURIComponent(id)}/test`,
    {method: 'POST'},
  );

export const getThresholds = (signal?: AbortSignal) =>
  request<RawThresholds>('/api/settings/thresholds', {signal});

export const updateThresholds = (body: {
  cpu?: Record<string, number>;
  memory?: Record<string, number>;
  disk?: Record<string, number>;
}) =>
  request<RawThresholds & {ok: boolean; error?: string}>(
    '/api/settings/thresholds',
    {method: 'PUT', json: body},
  );

export const getFilterRules = (signal?: AbortSignal) =>
  request<RawFilterRules>('/api/settings/filter_rules', {signal});
