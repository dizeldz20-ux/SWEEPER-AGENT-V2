export type ViewState =
  | 'dashboard'
  | 'history'
  | 'machines'
  | 'approvals'
  | 'chat'
  | 'settings';

export interface Machine {
  id: string;
  name: string;
  ip: string;
  /** Agent-side host kind: the local agent host vs. a remote SSM connector. */
  kind: 'local' | 'connector';
  status: 'online' | 'offline' | 'warning';
  cpuUsage: number;
  memoryUsage: number;
  lastPing: string;
  lastUpdate: string;
  type: 'pbx' | 'server';
  maintenanceMode?: boolean;
  maintenanceEndTime?: string;
}

export interface Alert {
  id: string;
  machineId: string;
  machineName: string;
  level: 'info' | 'warning' | 'critical';
  message: string;
  timestamp: string;
  eventType: 'security' | 'system' | 'performance' | 'network';
  status: 'unread' | 'in-progress' | 'resolved';
  acknowledged: boolean;
  snoozedUntil?: string;
  priority: 'low' | 'medium' | 'high' | 'urgent';
}

export interface FilterRule {
  id: string;
  name: string;
  pattern: string;
  action: 'alert' | 'ignore' | 'log';
  enabled: boolean;
  recoveryAction?: 'none' | 'restart_service' | 'run_script';
  recoveryScript?: string;
  enforced?: boolean;
}

export interface Approval {
  id: string;
  action: string;
  reason: string;
  proposedCommand: string;
  createdAt: string;
  status: string;
  server: string;
}

export interface Blocked {
  key: string;
  action: string;
  server: string;
  reason: string;
  blockedAt: string;
}
