import {useState, useEffect, useRef, type ReactNode} from 'react';
import {Machine, Alert} from '../types';
import {AlertTriangle, CheckCircle2, Clock, LayoutDashboard, PhoneCall, Server} from 'lucide-react';

import {MetricsChart} from './MetricsChart';
import {CpuSparklineWidget} from './CpuSparklineWidget';
import {HeatmapWidget} from './HeatmapWidget';
import {LogStreamWidget} from './LogStreamWidget';
import {AgentHealthPanel} from './AgentHealthPanel';
import {Dropdown} from './Dropdown';

interface DashboardProps {
  machines: Machine[];
  alerts: Alert[];
  onUpdateAlertStatus?: (id: string, status: Alert['status']) => void;
  onSnoozeAlert?: (id: string, durationMinutes: number) => void;
}

export function Dashboard({machines, alerts, onUpdateAlertStatus, onSnoozeAlert}: DashboardProps) {
  const [activeTab, setActiveTab] = useState<'all' | 'network' | 'performance' | 'security' | 'system'>('all');
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 10000);
    return () => clearInterval(timer);
  }, []);

  const isSnoozed = (a: Alert) => a.snoozedUntil && new Date(a.snoozedUntil) > now;
  const visibleAlerts = alerts.filter((a) => a.status !== 'resolved' && !isSnoozed(a));

  const activeAlerts = visibleAlerts.filter((a) => activeTab === 'all' || a.eventType === activeTab);
  const criticalCount = visibleAlerts.filter((a) => a.level === 'critical').length;
  const warningCount = visibleAlerts.filter((a) => a.level === 'warning').length;
  const infoCount = visibleAlerts.filter((a) => a.level === 'info').length;
  const onlineCount = machines.filter((m) => m.status === 'online').length;
  const pbxCount = machines.filter((m) => m.type === 'pbx').length;

  const [pulseCritical, setPulseCritical] = useState(false);
  const prevCriticalCount = useRef(criticalCount);

  useEffect(() => {
    if (criticalCount > prevCriticalCount.current) {
      setPulseCritical(true);
      const timer = setTimeout(() => setPulseCritical(false), 3000);
      return () => clearTimeout(timer);
    }
    prevCriticalCount.current = criticalCount;
  }, [criticalCount]);

  return (
    <div className="p-6 lg:p-8 flex flex-col gap-6 view-in" dir="rtl">
      {/* Header: identity + live status */}
      <header className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-4">
          <div className="icon-tile h-12 w-12 bg-indigo-500/12 text-indigo-300">
            <LayoutDashboard className="h-6 w-6" />
          </div>
          <div>
            <h2 className="text-[26px] font-extrabold tracking-tight text-white leading-tight">מבט על המערכת</h2>
            <p className="text-sm text-slate-400 mt-1">מעקב בזמן אמת אחר <span className="num font-semibold text-slate-200">{machines.length}</span> שרתים ומרכזיות</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="chip bg-white/[0.03] text-slate-300">
            <Clock className="h-3.5 w-3.5 text-slate-400" />
            <span className="num" dir="ltr">{now.toLocaleTimeString('he-IL', {hour: '2-digit', minute: '2-digit'})}</span>
          </span>
          <span className="chip bg-rose-500/10 border-rose-500/25 text-rose-300">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-rose-500" />
            </span>
            סריקה פעילה
          </span>
        </div>
      </header>

      {/* KPI row — four uniform tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5 stagger">
        <StatCard
          label='סה"כ מכונות'
          value={machines.length}
          icon={<Server className="h-6 w-6 text-indigo-400" />}
          iconBg="bg-indigo-500/10"
          footer={<OnlineBar online={onlineCount} total={machines.length} />}
        />
        <StatCard
          label="מרכזיות (PBX)"
          value={pbxCount}
          icon={<PhoneCall className="h-6 w-6 text-purple-400" />}
          iconBg="bg-purple-500/10"
          footer={<div className="text-xs text-slate-500">מרכזיות FreeSWITCH במעקב</div>}
        />
        <AlertsSummaryCard
          total={visibleAlerts.length}
          critical={criticalCount}
          warning={warningCount}
          info={infoCount}
          pulse={pulseCritical}
        />
        <CpuSparklineWidget machines={machines} />
      </div>

      {/* Agent self-health — the agent watching its own machine + alert channels,
          always-on and independent of the monitored fleet above. */}
      <AgentHealthPanel />

      {/* Analytics + operations bento */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <div className="lg:col-span-2">
          <MetricsChart alerts={alerts} />
        </div>
        <div className="lg:col-span-2">
          <HeatmapWidget />
        </div>

        {/* Live alerts feed */}
        <div className="lg:col-span-3 panel p-6 flex flex-col overflow-hidden h-[420px]">
          <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-5">
            <div className="flex items-center gap-2.5">
              <span className="relative flex h-2.5 w-2.5">
                <span className="absolute inline-flex h-full w-full rounded-full bg-rose-500 opacity-60 animate-ping" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-rose-500" />
              </span>
              <h3 className="text-[17px] font-bold text-white">יומן אירועים חי <span className="text-slate-500 font-mono text-xs font-normal">(Real-time)</span></h3>
            </div>
            <div className="seg" dir="rtl">
              {(['all', 'network', 'performance', 'security', 'system'] as const).map((tab) => (
                <button key={tab} onClick={() => setActiveTab(tab)} data-active={activeTab === tab} className="seg-item">
                  {tab === 'all' ? 'הכל' : tab === 'network' ? 'רשת' : tab === 'performance' ? 'ביצועים' : tab === 'security' ? 'אבטחה' : 'מערכת'}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 space-y-2.5 overflow-y-auto pl-1 -ml-1">
            {activeAlerts.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-slate-500 gap-3">
                <div className="icon-tile w-14 h-14 bg-emerald-500/10 text-emerald-400/70">
                  <CheckCircle2 className="w-7 h-7" />
                </div>
                <p className="text-sm">אין התראות פעילות</p>
              </div>
            ) : (
              activeAlerts.map((alert) => (
                <div
                  key={alert.id}
                  className={`group flex flex-col md:flex-row md:items-center justify-between gap-3 p-3.5 rounded-xl bg-white/[0.02] hover:bg-white/[0.045] border border-white/[0.04] border-r-[3px] transition-colors ${
                    alert.level === 'critical' ? 'border-r-rose-500' : alert.level === 'warning' ? 'border-r-amber-500' : 'border-r-emerald-500'
                  }`}
                >
                  <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-3.5 flex-1 min-w-0">
                    <div className="flex gap-2 items-center min-w-max font-mono">
                      <span className="text-slate-500 text-xs num" dir="ltr">
                        [{new Date(alert.timestamp).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}]
                      </span>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded font-bold uppercase tracking-wider ${
                          alert.priority === 'urgent'
                            ? 'bg-red-600 text-white shadow-[0_0_12px_-2px_rgba(220,38,38,0.8)]'
                            : alert.priority === 'high'
                              ? 'bg-orange-500 text-white'
                              : alert.priority === 'medium'
                                ? 'bg-yellow-500/80 text-white'
                                : 'bg-slate-600 text-white'
                        }`}
                      >
                        {alert.priority}
                      </span>
                      <span
                        className={`text-xs font-semibold ${
                          alert.level === 'critical' ? 'text-rose-400' : alert.level === 'warning' ? 'text-amber-400' : 'text-emerald-400'
                        }`}
                      >
                        [{alert.level.toUpperCase()}]
                      </span>
                    </div>
                    <span className="text-slate-300 text-sm truncate">
                      <span className="font-semibold text-slate-100">{alert.machineName}:</span> {alert.message}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 self-end md:self-center shrink-0">
                    <Dropdown
                      ariaLabel="השהה התראה"
                      width={128}
                      triggerClassName="text-xs px-2.5 py-1.5 bg-white/[0.04] text-slate-400 hover:bg-white/[0.08] hover:text-slate-200 rounded-lg transition-colors border border-white/[0.06] whitespace-nowrap"
                      trigger="השהה"
                    >
                      {(close) => (
                        <>
                          <button onClick={() => { close(); onSnoozeAlert?.(alert.id, 15); }} className="w-full text-right px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors border-b border-white/[0.05]">15 דקות</button>
                          <button onClick={() => { close(); onSnoozeAlert?.(alert.id, 60); }} className="w-full text-right px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors border-b border-white/[0.05]">שעה 1</button>
                          <button onClick={() => { close(); onSnoozeAlert?.(alert.id, 24 * 60); }} className="w-full text-right px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors">24 שעות</button>
                        </>
                      )}
                    </Dropdown>
                    {alert.status === 'unread' && (
                      <button
                        onClick={() => onUpdateAlertStatus?.(alert.id, 'in-progress')}
                        className="text-xs px-2.5 py-1.5 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 rounded-lg transition-colors border border-amber-500/25 whitespace-nowrap"
                      >
                        סמן בטיפול
                      </button>
                    )}
                    <button
                      onClick={() => onUpdateAlertStatus?.(alert.id, 'resolved')}
                      className="text-xs px-2.5 py-1.5 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 rounded-lg transition-colors border border-emerald-500/25 whitespace-nowrap"
                    >
                      סמן כנפתר
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Machine status list */}
        <div className="lg:col-span-1 panel p-6 flex flex-col h-[420px] overflow-hidden">
          <div className="flex items-center justify-between mb-5">
            <h3 className="text-[17px] font-bold text-white">סטטוס מכונות</h3>
            <span className="chip bg-white/[0.03] text-slate-400 num" dir="ltr">{onlineCount}/{machines.length}</span>
          </div>
          <div className="space-y-2.5 overflow-y-auto pl-1 -ml-1">
            {machines.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-500">
                <div className="icon-tile w-14 h-14 bg-white/[0.04] text-slate-500"><Server className="h-7 w-7" /></div>
                <p className="text-sm">אין מכונות מוגדרות</p>
              </div>
            ) : (
              machines.map((machine) => (
                <div
                  key={machine.id}
                  className={`flex items-center justify-between p-3 bg-white/[0.02] hover:bg-white/[0.04] rounded-xl border transition-colors ${
                    machine.status === 'warning' ? 'border-amber-500/25' : machine.status === 'offline' ? 'border-rose-500/25' : 'border-white/[0.04]'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="relative flex h-2 w-2 shrink-0">
                      {machine.status !== 'online' && <span className={`absolute inline-flex h-full w-full rounded-full opacity-60 animate-ping ${machine.status === 'warning' ? 'bg-amber-400' : 'bg-rose-500'}`} />}
                      <span
                        className={`relative inline-flex h-2 w-2 rounded-full ${
                          machine.status === 'online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.7)]' : machine.status === 'warning' ? 'bg-amber-500' : 'bg-rose-500'
                        }`}
                      />
                    </span>
                    <span className="truncate text-sm text-slate-200">{machine.name}</span>
                  </div>
                  <span
                    className={`shrink-0 text-xs font-mono num ${
                      machine.status === 'warning' ? 'text-amber-400' : machine.status === 'offline' ? 'text-rose-400' : 'text-slate-500'
                    }`}
                    dir="ltr"
                  >
                    {machine.status === 'online' ? machine.ip : machine.status === 'warning' ? 'LATENCY' : 'OFFLINE'}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Real-time log stream */}
        <div className="lg:col-span-4">
          <LogStreamWidget />
        </div>
      </div>
    </div>
  );
}

// One KPI tile — uniform header (label + big value + corner icon) with a
// flexible footer slot that keeps every tile the same height.
function StatCard({
  label,
  value,
  icon,
  iconBg,
  footer,
}: {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  iconBg: string;
  footer: ReactNode;
}) {
  return (
    <div className="panel panel-hover flex h-full flex-col justify-between p-5">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h3 className="text-[13px] font-medium text-slate-400">{label}</h3>
          <div className="mt-2.5 text-[34px] leading-none font-extrabold text-white num" dir="ltr">{value}</div>
        </div>
        <div className={`icon-tile h-12 w-12 ${iconBg}`}>{icon}</div>
      </div>
      <div className="mt-4 border-t border-white/[0.06] pt-4">{footer}</div>
    </div>
  );
}

// Online/total ratio bar for the machines tile.
function OnlineBar({online, total}: {online: number; total: number}) {
  const pct = total ? Math.round((online / total) * 100) : 0;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs">
        <span className="font-semibold text-emerald-400"><span className="num">{online}</span> מקוונות</span>
        <span className="text-slate-500 num" dir="ltr">{online}/{total}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
        <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 shadow-[0_0_10px_-2px_rgba(16,185,129,0.8)] transition-all duration-500" style={{width: `${pct}%`}} />
      </div>
    </div>
  );
}

const ALERT_TONES = {
  rose: 'bg-rose-500/10 border-rose-500/20 text-rose-400',
  amber: 'bg-amber-500/10 border-amber-500/20 text-amber-400',
  emerald: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400',
} as const;

// Active-alerts tile: big total, then a compact severity breakdown. Critical
// count pulses when a new critical alert lands (mirrors prior behavior).
function AlertsSummaryCard({
  total,
  critical,
  warning,
  info,
  pulse,
}: {
  total: number;
  critical: number;
  warning: number;
  info: number;
  pulse: boolean;
}) {
  return (
    <div
      className={`panel ${pulse ? '' : 'panel-hover'} flex h-full flex-col justify-between p-5 transition-all ${
        pulse ? 'pulse-glow border-rose-500/50' : ''
      }`}
    >
      <div className="mb-4 flex items-start justify-between">
        <div>
          <h3 className="text-[13px] font-medium text-slate-400">התראות פעילות</h3>
          <div className="mt-2.5 text-[34px] leading-none font-extrabold text-white num" dir="ltr">{total}</div>
        </div>
        <div className="icon-tile h-12 w-12 bg-amber-500/12 text-amber-300">
          <AlertTriangle className="h-6 w-6" />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 border-t border-white/[0.06] pt-4">
        <MiniStat value={critical} label="קריטי" tone="rose" pulse={pulse} />
        <MiniStat value={warning} label="אזהרה" tone="amber" />
        <MiniStat value={info} label="מידע" tone="emerald" />
      </div>
    </div>
  );
}

function MiniStat({value, label, tone, pulse}: {value: number; label: string; tone: keyof typeof ALERT_TONES; pulse?: boolean}) {
  return (
    <div
      className={`flex flex-col items-center justify-center rounded-xl border py-2 ${ALERT_TONES[tone]} ${
        pulse && tone === 'rose' ? 'ring-2 ring-rose-500/40' : ''
      }`}
    >
      <span className="text-xl font-bold leading-none num">{value}</span>
      <span className="mt-1 text-[10px] font-semibold uppercase tracking-wide opacity-80">{label}</span>
    </div>
  );
}
