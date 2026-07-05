// The machines view: a grid of "rack blade" cards, one per monitored host.
// Each card is a compact instrument face — status hairline on the machined top
// edge, server housing with a status glow, live telemetry — and clicking it
// opens the machine console (MachineDetailModal) for full detail + checks
// configuration. Quick actions stay one click away on the card itself.

import {Fragment, useState, type ReactNode} from 'react';
import {Machine} from '../types';
import {Server, Clock, ChevronLeft, Cpu, MemoryStick} from 'lucide-react';
import {UptimeChartWidget} from './UptimeChartWidget';
import {MachineDetailModal} from './MachineDetailModal';
import {STATUS_META} from './statusMeta';

interface MachineListProps {
  machines: Machine[];
}

export function MachineList({ machines }: MachineListProps) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Look the machine up fresh each render so the open console keeps tracking
  // live polling updates (status flips, maintenance, metrics).
  const selected = selectedId ? machines.find((m) => m.id === selectedId) ?? null : null;

  return (
    <div className="p-6 lg:p-8 flex flex-col gap-6 view-in" dir="rtl">
      <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h2 className="text-[26px] font-extrabold tracking-tight text-white">מכונות מנוטרות</h2>
          <p className="text-sm text-slate-400 mt-1">רשימה בזמן אמת של כל השרתים והמרכזיות — לחיצה על מכונה פותחת את כרטיס המכונה</p>
        </div>
        <span className="chip bg-white/[0.03] text-slate-300 num" dir="ltr">{machines.length} hosts</span>
      </header>

      <UptimeChartWidget />

      {machines.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-3xl border border-dashed border-white/10 bg-slate-900/50 px-6 py-16 text-center">
          <Server className="h-9 w-9 text-slate-600" />
          <div className="font-display text-base font-semibold text-slate-300">אין מכונות מנוטרות עדיין</div>
          <div className="text-sm text-slate-500">הוסף מכונה ראשונה דרך מסך ההגדרות ותראה אותה כאן כרטיסייה חיה.</div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
          {machines.map((machine, i) => (
            <Fragment key={machine.id}>
              <MachineCard
                machine={machine}
                index={i}
                onOpen={() => setSelectedId(machine.id)}
              />
            </Fragment>
          ))}
        </div>
      )}

      {selected ? <MachineDetailModal machine={selected} onClose={() => setSelectedId(null)} /> : null}
    </div>
  );
}

function MachineCard({
  machine,
  index,
  onOpen,
}: {
  machine: Machine;
  index: number;
  onOpen: () => void;
}) {
  const meta = STATUS_META[machine.status];

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`פתח את כרטיס המכונה ${machine.name}`}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}
      style={{animationDelay: `${Math.min(index, 8) * 60}ms`}}
      className={`wizard-rise-in group relative cursor-pointer overflow-hidden rounded-2xl ring-1 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-black/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
        machine.maintenanceMode
          ? 'bg-slate-900 ring-amber-500/25 hover:ring-amber-400/50'
          : 'bg-slate-900 ring-white/10 hover:ring-indigo-400/40'
      }`}
    >
      {/* Machined top edge in the machine's status color — same visual language
          as the console modal's hairline. */}
      <div className={`pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-l from-transparent ${meta.hairline} to-transparent`} />
      {machine.maintenanceMode ? (
        <div className="pointer-events-none absolute inset-0 bg-amber-500/5" />
      ) : null}

      <div className="relative flex flex-col gap-4 p-5">
        {/* Identity row: housing + name + status */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3.5">
            <div className={`shrink-0 rounded-xl bg-slate-800 p-3 ring-1 ring-white/5 ${meta.glow}`}>
              <Server className={`h-6 w-6 ${meta.icon}`} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="truncate font-display text-lg font-semibold text-white">{machine.name}</span>
                <ChevronLeft className="h-4 w-4 shrink-0 -translate-x-1 text-indigo-400 opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100" />
              </div>
              <div className="mt-0.5 truncate font-mono text-[11px] text-slate-500" dir="ltr">
                {machine.ip || 'local host'}
              </div>
            </div>
          </div>
          <span className={`inline-flex shrink-0 items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${meta.chip}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${meta.dot} ${meta.dotExtra}`} />
            {meta.label}
          </span>
        </div>

        {/* Telemetry: live gauges for the local host, collection age for connectors. */}
        {machine.kind === 'local' ? (
          <div className="grid grid-cols-2 gap-3">
            <MicroGauge icon={<Cpu className="h-3.5 w-3.5" />} label="CPU" pct={machine.cpuUsage} />
            <MicroGauge icon={<MemoryStick className="h-3.5 w-3.5" />} label="זיכרון" pct={machine.memoryUsage} />
          </div>
        ) : (
          <div className="flex items-center gap-2 rounded-xl border border-white/5 bg-slate-950/40 px-3.5 py-2.5 text-xs text-slate-400">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">איסוף אחרון</span>
            <span className="font-display text-sm font-semibold tabular-nums text-slate-200" dir="ltr">{machine.lastPing}</span>
          </div>
        )}

        {machine.maintenanceMode ? (
          <div className="flex items-center gap-2 text-[11px] text-amber-500">
            <span className="inline-flex items-center rounded-full border border-amber-500/30 bg-amber-500/20 px-2.5 py-0.5 font-bold uppercase tracking-wider">
              תחזוקה
            </span>
            {machine.maintenanceEndTime ? (
              <span className="text-amber-500/70">
                מסתיים ב-{new Date(machine.maintenanceEndTime).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}
              </span>
            ) : null}
          </div>
        ) : null}

        {/* Footer: last update + a hint that actions live in the console. The
            card is read-only; opening it (click) is the single path to the
            machine's checks and controls. */}
        <div className="flex items-center justify-between border-t border-white/5 pt-3.5">
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Clock className="h-3.5 w-3.5" />
            <span dir="ltr">
              {new Date(machine.lastUpdate).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}
            </span>
          </div>

          <span className="flex items-center gap-1 text-[11px] font-medium text-slate-500 transition-colors group-hover:text-indigo-300">
            פרטים ופעולות
            <ChevronLeft className="h-3.5 w-3.5" />
          </span>
        </div>
      </div>
    </div>
  );
}

// A labeled micro progress bar: icon + label right, live % left, bar below.
function MicroGauge({icon, label, pct}: {icon: ReactNode; label: string; pct: number}) {
  const tone = pct >= 90 ? 'bg-rose-400' : pct >= 70 ? 'bg-amber-400' : 'bg-emerald-400';
  return (
    <div className="rounded-xl border border-white/5 bg-slate-950/40 px-3.5 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
          {icon}
          {label}
        </span>
        <span className="font-display text-sm font-semibold tabular-nums text-slate-200" dir="ltr">
          {pct}%
        </span>
      </div>
      <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-700/50">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${tone}`}
          style={{width: `${Math.min(100, pct)}%`}}
        />
      </div>
    </div>
  );
}
