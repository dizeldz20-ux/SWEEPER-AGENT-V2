// Translate raw agent responses into the dashboard's UI types.
//
// The agent's data model (fleet hosts + pipeline snapshot + monitor-audit
// alerts) does not map 1:1 onto the mock-era UI types, so some UI fields are
// derived/defaulted. Each such case is commented.

import type {Alert, Approval, Blocked, Machine} from '../types';
import type {
  RawBlocked,
  RawFleet,
  RawFleetHost,
  RawModule,
  RawProposal,
  RawSnapshot,
  RawV6Alert,
} from './agentTypes';

// --- small helpers -----------------------------------------------------------

// Deterministic id from stable fields (so React keys + resolve/snooze targets
// stay constant across polls). djb2 -> base36.
export function stableId(...parts: (string | number)[]): string {
  const s = parts.join('|');
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

function pickNumber(
  values: Record<string, unknown> | undefined,
  keys: string[],
): number | null {
  if (!values) return null;
  for (const k of keys) {
    const v = values[k];
    if (typeof v === 'number' && !Number.isNaN(v)) return v;
  }
  return null;
}

function clampPct(n: number | null): number {
  if (n === null) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

function epochToIso(ts: number | null | undefined): string | null {
  if (typeof ts !== 'number' || ts <= 0) return null;
  try {
    return new Date(ts * 1000).toISOString();
  } catch {
    return null;
  }
}

function relativeAge(iso: string | null): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}

// --- fleet + snapshot -> Machine[] ------------------------------------------

// The dashboard has only 3 states; the agent has 5. 'crit' is "reachable but
// bad" -> warning; 'error'/'unknown' means we couldn't reach the host -> offline.
function fleetStatusToUi(s: RawFleetHost['status']): Machine['status'] {
  switch (s) {
    case 'ok':
      return 'online';
    case 'warn':
    case 'crit':
      return 'warning';
    case 'error':
    case 'unknown':
    default:
      return 'offline';
  }
}

// Heuristic pbx/server classification — the agent has no such field.
function classifyType(name: string, modules?: Record<string, RawModule>): Machine['type'] {
  const n = (name || '').toLowerCase();
  if (/pbx|asterisk|freeswitch|\bfs\b|sip|voip/.test(n)) return 'pbx';
  if (modules && Object.keys(modules).some((k) => /freeswitch|fs|sip/.test(k.toLowerCase()))) {
    return 'pbx';
  }
  return 'server';
}

function localMetrics(snapshot?: RawSnapshot | null): {cpu: number; mem: number} {
  const modules = snapshot?.diagnosis?.modules || snapshot?.modules;
  const cpuVals = modules?.cpu?.values;
  const memVals = modules?.memory?.values || modules?.mem?.values;
  // CPU: prefer an explicit usage %, else derive from idle %, else 0.
  let cpu = pickNumber(cpuVals, [
    'cpu.usage_percent',
    'usage_percent',
    'used_percent',
    'cpu_percent',
  ]);
  if (cpu === null) {
    const idle = pickNumber(cpuVals, ['cpu.idle_percent', 'idle_percent']);
    if (idle !== null) cpu = 100 - idle;
  }
  const mem = pickNumber(memVals, ['memory.used_percent', 'used_percent', 'mem.used_percent']);
  return {cpu: clampPct(cpu), mem: clampPct(mem)};
}

export function fleetToMachines(fleet: RawFleet, snapshot?: RawSnapshot | null): Machine[] {
  return (fleet.hosts || []).map((h): Machine => {
    const isLocal = h.kind === 'local';
    const lastUpdate =
      h.last_seen || epochToIso(h.last_collected_at) || new Date().toISOString();
    const uiStatus = fleetStatusToUi(h.status);
    const metrics = isLocal ? localMetrics(snapshot) : {cpu: 0, mem: 0};
    const modules = snapshot?.diagnosis?.modules || snapshot?.modules;

    return {
      id: h.name,
      name: isLocal ? snapshot?.server || h.name : h.name,
      kind: isLocal ? 'local' : 'connector',
      // No IP in the agent model — show the EC2 instance id for connectors.
      ip: isLocal ? '' : h.instance_id || '',
      status: uiStatus,
      cpuUsage: metrics.cpu,
      memoryUsage: metrics.mem,
      lastPing: uiStatus === 'offline' ? 'Timeout' : relativeAge(lastUpdate),
      lastUpdate,
      type: classifyType(h.name, isLocal ? modules : undefined),
      // maintenance is tracked optimistically client-side (see useAlertOverlay);
      // the fleet API does not report it yet.
    };
  });
}

// --- v6 alerts -> Alert[] ----------------------------------------------------

function levelFromStatus(status: string): Alert['level'] {
  const s = (status || '').toLowerCase();
  if (s === 'crit' || s === 'red' || s === 'orange') return 'critical';
  if (s === 'warn' || s === 'yellow') return 'warning';
  return 'info';
}

function priorityFromLevel(level: Alert['level']): Alert['priority'] {
  if (level === 'critical') return 'urgent';
  if (level === 'warning') return 'high';
  return 'low';
}

function eventTypeFromTab(tab: string): Alert['eventType'] {
  switch ((tab || '').toLowerCase()) {
    case 'network':
      return 'network';
    case 'performance':
      return 'performance';
    case 'security':
      return 'security';
    case 'system':
    case 'other':
    default:
      return 'system';
  }
}

// The /v6/alerts feed has no id/message/priority and no server-side resolved
// state — all synthesized here; status/ack/snooze live in the client overlay.
export function alertToUi(raw: RawV6Alert): Alert {
  const level = levelFromStatus(raw.status);
  const host = raw.host && raw.host !== '—' ? raw.host : 'local';
  const id = stableId(raw.ts, raw.module, host, raw.status);
  return {
    id,
    machineId: host,
    machineName: host,
    level,
    message: `מודול "${raw.module}" דיווח על סטטוס ${raw.status}`,
    timestamp: raw.ts,
    eventType: eventTypeFromTab(raw.tab),
    status: 'unread',
    acknowledged: false,
    priority: priorityFromLevel(level),
  };
}

export function alertsToUi(raws: RawV6Alert[]): Alert[] {
  return (raws || []).map(alertToUi);
}

export function approvalsToUi(raws: RawProposal[]): Approval[] {
  return (raws || []).map((p) => ({
    id: p.id,
    action: p.action,
    reason: p.reason || '',
    proposedCommand: p.proposed_command || '',
    createdAt: p.created_at,
    status: p.status,
    server: p.server || '',
  }));
}

export function blockedToUi(raws: RawBlocked[]): Blocked[] {
  return (raws || []).map((b) => ({
    key: b.key,
    action: b.action,
    server: b.server || '',
    reason: b.reason || '',
    blockedAt: b.blocked_at,
  }));
}
