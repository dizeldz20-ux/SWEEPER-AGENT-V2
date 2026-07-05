import type {ReactNode} from 'react';
import {HardDrive, HeartPulse, MessageSquare, Send, Clock, ShieldAlert} from 'lucide-react';
import {usePolling} from '../hooks/usePolling';
import {getSelfHealth, type RawSelfBot, type RawSelfHealth} from '../services/endpoints';

// The agent watching ITSELF: machine resilience + notification-bot connectivity.
// Always-on background self-monitoring, surfaced here so the operator can see at
// a glance whether the agent's own machine and its alert channels are healthy.
// This is deliberately separate from the monitored-fleet view.

const DEFCON_TONE: Record<string, {ring: string; text: string; label: string}> = {
  ok: {ring: 'border-emerald-500/30 bg-emerald-500/10', text: 'text-emerald-300', label: 'תקין'},
  warn: {ring: 'border-amber-500/30 bg-amber-500/10', text: 'text-amber-300', label: 'אזהרה'},
  crit: {ring: 'border-rose-500/40 bg-rose-500/10', text: 'text-rose-300', label: 'קריטי'},
  unknown: {ring: 'border-white/10 bg-white/[0.03]', text: 'text-slate-400', label: 'לא ידוע'},
};

function defconKey(defcon: number): 'ok' | 'warn' | 'crit' | 'unknown' {
  if (defcon <= 2) return 'crit';
  if (defcon === 3) return 'unknown';
  if (defcon === 4) return 'warn';
  return 'ok';
}

const BOT_STATUS_TONE: Record<string, string> = {
  ok: 'text-emerald-400',
  warn: 'text-amber-400',
  crit: 'text-rose-400',
  disabled: 'text-slate-500',
  unknown: 'text-slate-500',
};

const BOT_STATUS_LABEL: Record<string, string> = {
  ok: 'מחובר',
  warn: 'בעיה זמנית',
  crit: 'נכשל',
  disabled: 'לא מוגדר',
  unknown: 'לא ידוע',
};

function formatUptime(seconds: number): string {
  if (!seconds || seconds <= 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  if (d > 0) return `${d}d ${h}h`;
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

export function AgentHealthPanel() {
  const {data, error} = usePolling<RawSelfHealth>(getSelfHealth, 15000);

  const defcon = data?.self_defcon ?? 3;
  const tone = DEFCON_TONE[defconKey(defcon)];
  const bots = data?.bots?.items ?? [];
  const diskPct = data?.state_dir_pct;
  const watchdog = data?.watchdog_restart_count ?? 0;

  return (
    <section className={`panel border ${tone.ring} p-5`} dir="rtl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className={`icon-tile h-11 w-11 ${tone.ring} ${tone.text}`}>
            <HeartPulse className="h-5 w-5" />
          </div>
          <div>
            <h3 className="text-[15px] font-bold text-white">בריאות הסוכן</h3>
            <p className="text-xs text-slate-400 mt-0.5">ניטור עצמי רציף — מכונת הסוכן וערוצי ההתראות</p>
          </div>
        </div>
        <span className={`chip border ${tone.ring} ${tone.text} font-semibold`}>
          <ShieldAlert className="h-3.5 w-3.5" />
          {error ? 'לא זמין' : tone.label}
        </span>
      </div>

      {/* Metric chips: state-dir disk, uptime, watchdog */}
      <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-3">
        <MetricChip
          icon={<HardDrive className="h-4 w-4" />}
          label="דיסק state"
          value={diskPct == null ? '—' : `${Math.round(diskPct)}%`}
          alert={diskPct != null && diskPct >= 80}
        />
        <MetricChip
          icon={<Clock className="h-4 w-4" />}
          label="Uptime"
          value={formatUptime(data?.uptime_seconds ?? 0)}
        />
        <MetricChip
          icon={<ShieldAlert className="h-4 w-4" />}
          label="Restarts/שעה"
          value={String(watchdog)}
          alert={watchdog >= 3}
        />
      </div>

      {/* Bot connectivity */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-300">חיבורי בוטים להתראות</span>
          {data?.bots ? (
            <span className="text-[11px] text-slate-500 num" dir="ltr">{data.bots.summary}</span>
          ) : null}
        </div>
        {bots.length === 0 ? (
          <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-3 text-xs text-slate-500">
            לא מוגדרים בוטים. הוסף בוט Telegram או Slack בהגדרות כדי לקבל התראות.
          </div>
        ) : (
          <div className="space-y-1.5">
            {bots.map((b) => (
              <BotRow key={`${b.platform}:${b.id}`} bot={b} />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function MetricChip({icon, label, value, alert}: {icon: ReactNode; label: string; value: string; alert?: boolean}) {
  return (
    <div className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 ${
      alert ? 'border-amber-500/30 bg-amber-500/10' : 'border-white/[0.05] bg-white/[0.02]'
    }`}>
      <span className={alert ? 'text-amber-300' : 'text-slate-500'}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
        <div className={`text-sm font-semibold num ${alert ? 'text-amber-200' : 'text-slate-200'}`} dir="ltr">{value}</div>
      </div>
    </div>
  );
}

function BotRow({bot}: {bot: RawSelfBot}) {
  const tone = BOT_STATUS_TONE[bot.status] ?? 'text-slate-500';
  const label = BOT_STATUS_LABEL[bot.status] ?? bot.status;
  const Icon = bot.platform === 'slack' ? MessageSquare : Send;
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3.5 py-2.5">
      <div className="flex items-center gap-2.5 min-w-0">
        <Icon className={`h-4 w-4 shrink-0 ${tone}`} />
        <span className="truncate text-sm text-slate-200">{bot.name}</span>
        <span className="shrink-0 rounded-md bg-white/[0.04] px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-500">
          {bot.platform}
        </span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {bot.identity ? (
          <span className="hidden truncate font-mono text-[11px] text-slate-500 sm:inline" dir="ltr">{bot.identity}</span>
        ) : null}
        <span className={`flex items-center gap-1.5 text-xs font-semibold ${tone}`}>
          <span className="relative flex h-1.5 w-1.5">
            <span className={`inline-flex h-1.5 w-1.5 rounded-full ${
              bot.status === 'ok' ? 'bg-emerald-400' : bot.status === 'crit' ? 'bg-rose-500' : bot.status === 'warn' ? 'bg-amber-400' : 'bg-slate-600'
            }`} />
          </span>
          {label}
        </span>
      </div>
    </div>
  );
}
