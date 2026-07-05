// The machine "console" — a floating control panel opened from a machine card.
// Two areas, per the operator workflow:
//   1. Live status: the machine's current state + a manual on-demand check.
//   2. Checks configuration: the tests configured for THIS machine, editable
//      in place, plus adding new ones from the shared catalog.

import {Fragment, useEffect, useMemo, useState} from 'react';
import {
  Activity,
  BrainCircuit,
  Check,
  CheckCircle2,
  Clock,
  Download,
  FileArchive,
  ListChecks,
  Loader2,
  Plus,
  Power,
  RefreshCw,
  Search,
  Wrench,
  XCircle,
} from 'lucide-react';
import {Modal} from './Modal';
import {useData} from '../context/DataContext';
import {usePolling} from '../hooks/usePolling';
import type {Machine} from '../types';
import type {RawFleetHost, RawHostConfig, RawModuleInfo} from '../services/agentTypes';
import {
  evidenceExportUrl,
  getFleetHost,
  getHostConfig,
  getPredictions,
  logDownloadUrl,
  runSweep,
  saveHostConfig,
  testConnector,
} from '../services/endpoints';
import {ApiError} from '../services/http';
import {
  DEFAULT_INTERVAL,
  TestRow,
  defaultTestState,
  groupByDomain,
  loadMonitorCatalog,
  type TestState,
} from './checksCatalog';
import {STATUS_META} from './statusMeta';

interface MachineDetailModalProps {
  machine: Machine;
  onClose: () => void;
}

interface CheckOutcome {
  ok: boolean;
  message: string;
  at: string; // HH:MM:SS
}

// The console's four areas, navigated by tabs. "Checks" is the only tab with a
// save action, so the modal footer is shown for it alone.
type TabId = 'status' | 'checks' | 'predictions' | 'evidence';

const now = () => new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});

export function MachineDetailModal({machine, onClose}: MachineDetailModalProps) {
  const {toggleMaintenance, requestMachineAction, refetch} = useData();
  const meta = STATUS_META[machine.status];
  const [activeTab, setActiveTab] = useState<TabId>('status');

  // --- Area 1: live host detail + manual check -------------------------------
  const [detail, setDetail] = useState<RawFleetHost | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<CheckOutcome | null>(null);

  const fetchDetail = () => {
    void getFleetHost(machine.id)
      .then(setDetail)
      .catch(() => setDetail(null));
  };
  useEffect(fetchDetail, [machine.id]);

  const runManualCheck = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      if (machine.kind === 'local') {
        // Local host: scope the sweep to THIS machine's enabled checks and run
        // diagnose-only (no auto-repair) — a manual scan reports, it doesn't fix.
        const snap = await runSweep({host: machine.id, repair: false});
        const problems = typeof snap.problems_found === 'number' ? snap.problems_found : null;
        setCheckResult({
          ok: true,
          at: now(),
          message:
            problems === null
              ? 'הסריקה הסתיימה'
              : problems === 0
                ? 'הסריקה הסתיימה — לא נמצאו בעיות'
                : `הסריקה הסתיימה — נמצאו ${problems} בעיות`,
        });
      } else {
        // Connector: trigger a real SSM collection as proof of reachability.
        const res = await testConnector(machine.id);
        setCheckResult(
          res.ok
            ? {ok: true, at: now(), message: 'המכונה מגיבה — הנתונים נאספו דרך SSM'}
            : {ok: false, at: now(), message: res.error || 'בדיקת החיבור נכשלה'},
        );
      }
    } catch (err) {
      setCheckResult({ok: false, at: now(), message: err instanceof Error ? err.message : String(err)});
    } finally {
      setChecking(false);
      refetch();
      fetchDetail();
    }
  };

  // --- Area 2: per-machine checks configuration ------------------------------
  const [catalog, setCatalog] = useState<RawModuleInfo[]>([]);
  const [config, setConfig] = useState<RawHostConfig | null>(null);
  const [checksLoading, setChecksLoading] = useState(true);
  const [checksError, setChecksError] = useState('');
  const [selection, setSelection] = useState<Record<string, TestState>>({});
  const [baseline, setBaseline] = useState<Record<string, TestState>>({});
  const [view, setView] = useState<'active' | 'catalog'>('active');
  const [query, setQuery] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setChecksLoading(true);
    setChecksError('');
    void Promise.all([
      loadMonitorCatalog(),
      // A machine with no saved config yet is a valid state, not an error.
      getHostConfig(machine.id).catch((err) => {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }),
    ])
      .then(([monitors, cfg]) => {
        if (cancelled) return;
        setCatalog(monitors);
        setConfig(cfg);
        // Seed from catalog defaults (everything off), then overlay whatever
        // the host config actually has — enabled flags, intervals, thresholds.
        const seed: Record<string, TestState> = {};
        for (const m of monitors) seed[m.name] = defaultTestState(m, false);
        for (const mc of cfg?.monitors ?? []) {
          const base = seed[mc.name];
          if (!base) continue;
          const params = {...base.params};
          for (const key of Object.keys(base.params)) {
            const v = mc[key];
            if (typeof v === 'number') params[key] = v;
          }
          seed[mc.name] = {
            enabled: !!mc.enabled,
            interval: typeof mc.interval_sec === 'number' ? mc.interval_sec : base.interval,
            params,
          };
        }
        setSelection(seed);
        setBaseline(seed);
      })
      .catch((err) => {
        if (!cancelled) setChecksError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setChecksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [machine.id]);

  const dirty = useMemo(
    () => JSON.stringify(selection) !== JSON.stringify(baseline),
    [selection, baseline],
  );
  const enabledCount = useMemo(
    () => catalog.filter((m) => selection[m.name]?.enabled).length,
    [catalog, selection],
  );

  const setTestEnabled = (name: string, enabled: boolean) =>
    setSelection((prev) => ({...prev, [name]: {...prev[name], enabled}}));
  const setParam = (name: string, param: string, value: number) =>
    setSelection((prev) => ({
      ...prev,
      [name]: {...prev[name], params: {...prev[name].params, [param]: value}},
    }));
  const setTestInterval = (name: string, interval: number) =>
    setSelection((prev) => ({...prev, [name]: {...prev[name], interval}}));

  const save = async () => {
    setSaving(true);
    setSaveError('');
    try {
      const catalogNames = new Set(catalog.map((m) => m.name));
      const monitors = [
        ...catalog.map((m) => {
          const st = selection[m.name];
          return {
            name: m.name,
            enabled: st?.enabled ?? false,
            interval_sec: st?.interval ?? DEFAULT_INTERVAL,
            ...(st?.params ?? {}),
          };
        }),
        // Preserve config entries the catalog no longer lists, untouched.
        ...(config?.monitors ?? []).filter((mc) => !catalogNames.has(mc.name)),
      ];
      await saveHostConfig(machine.id, {
        description: config?.description ?? '',
        enabled: config?.enabled ?? true,
        monitors,
      });
      setBaseline(selection);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // Query + view filtering: "active" shows only enabled checks; "catalog"
  // shows everything so new checks can be switched on in place.
  const q = query.trim().toLowerCase();
  const groups = useMemo(() => {
    const matches = (m: RawModuleInfo) =>
      !q ||
      m.title_he.toLowerCase().includes(q) ||
      m.name.toLowerCase().includes(q) ||
      m.tags.some((t) => t.toLowerCase().includes(q));
    const visible = catalog.filter(
      (m) => matches(m) && (view === 'catalog' || selection[m.name]?.enabled),
    );
    return groupByDomain(visible);
  }, [catalog, selection, view, q]);

  // --- Chrome -----------------------------------------------------------------
  const identity = (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${meta.chip}`}>
        <span className={`h-1.5 w-1.5 rounded-full ${meta.dot} ${meta.dotExtra}`} />
        {meta.label}
      </span>
      <span className="font-mono text-xs text-slate-400" dir="ltr">
        {machine.ip || 'local host'}
      </span>
      {detail?.region ? (
        <span className="font-mono text-xs text-slate-500" dir="ltr">{detail.region}</span>
      ) : null}
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        <Clock className="h-3.5 w-3.5" />
        עדכון אחרון{' '}
        <span dir="ltr">
          {new Date(machine.lastUpdate).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}
        </span>
      </span>
      {machine.maintenanceMode ? (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/15 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-amber-400">
          <Wrench className="h-3 w-3" />
          תחזוקה
          {machine.maintenanceEndTime ? (
            <span className="font-mono font-normal normal-case tracking-normal" dir="ltr">
              → {new Date(machine.maintenanceEndTime).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}
            </span>
          ) : null}
        </span>
      ) : null}
    </div>
  );

  const footer = (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
        <span>
          <span className="font-display font-semibold tabular-nums text-indigo-300">{enabledCount}</span> בדיקות פעילות
        </span>
        {saved && !dirty && !saveError ? (
          <span className="flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 text-xs text-emerald-300">
            <CheckCircle2 className="h-3 w-3" />
            התצורה נשמרה
          </span>
        ) : null}
        {saveError ? (
          <span className="flex items-center gap-1.5 text-xs text-rose-300">
            <XCircle className="h-3.5 w-3.5 shrink-0" />
            {saveError}
          </span>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => void save()}
        disabled={!dirty || saving || checksLoading || !!checksError}
        className="flex items-center gap-2 rounded-xl bg-indigo-500 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 ring-1 ring-inset ring-white/15 transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
        שמור תצורה
      </button>
    </div>
  );

  return (
    <Modal
      open
      onClose={onClose}
      dismissable={!saving}
      widthClass="max-w-4xl"
      kicker={`כרטיס מכונה · ${machine.kind === 'local' ? 'מארח מקומי' : 'AWS SSM'}`}
      title={machine.name}
      subtitle={identity}
      footer={activeTab === 'checks' ? footer : undefined}
    >
      <div className="wizard-rise-in space-y-5">
        <TabBar active={activeTab} onChange={setActiveTab} enabledCount={enabledCount} />
        {activeTab === 'status' && (
          <StatusPanel
            machine={machine}
            detail={detail}
            checking={checking}
            checkResult={checkResult}
            onRunCheck={() => void runManualCheck()}
            onToggleMaintenance={() => toggleMaintenance(machine.id)}
            onAction={(a) => requestMachineAction(machine.id, a)}
          />
        )}
        {activeTab === 'checks' && (
          <ChecksPanel
            loading={checksLoading}
            error={checksError}
            groups={groups}
            selection={selection}
            enabledCount={enabledCount}
            view={view}
            setView={setView}
            query={query}
            setQuery={setQuery}
            onToggleTest={setTestEnabled}
            onSetParam={setParam}
            onSetInterval={setTestInterval}
          />
        )}
        {activeTab === 'predictions' && <PredictionsPanel host={machine.id} />}
        {activeTab === 'evidence' && <EvidencePanel host={machine.id} kind={machine.kind} />}
      </div>
    </Modal>
  );
}

// --- Tab bar ------------------------------------------------------------------

function TabBar({
  active,
  onChange,
  enabledCount,
}: {
  active: TabId;
  onChange: (tab: TabId) => void;
  enabledCount: number;
}) {
  const tabs: Array<{id: TabId; label: string; icon: typeof Activity; badge?: number}> = [
    {id: 'status', label: 'מצב', icon: Activity},
    {id: 'checks', label: 'בדיקות', icon: ListChecks, badge: enabledCount},
    {id: 'predictions', label: 'תחזיות', icon: BrainCircuit},
    {id: 'evidence', label: 'ראיות', icon: FileArchive},
  ];
  return (
    <div className="flex flex-wrap gap-1 rounded-2xl border border-white/10 bg-slate-950/40 p-1">
      {tabs.map((t) => {
        const Icon = t.icon;
        const isActive = active === t.id;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => onChange(t.id)}
            className={`flex flex-1 items-center justify-center gap-2 rounded-xl px-3.5 py-2 text-sm font-medium transition-colors ${
              isActive
                ? 'bg-indigo-500/15 text-white ring-1 ring-inset ring-indigo-400/30'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Icon className={`h-4 w-4 ${isActive ? 'text-indigo-300' : ''}`} />
            {t.label}
            {typeof t.badge === 'number' ? (
              <span className="rounded-md bg-indigo-500/20 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-indigo-300">
                {t.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

// --- Area 1: live status ------------------------------------------------------

function StatusPanel({
  machine,
  detail,
  checking,
  checkResult,
  onRunCheck,
  onToggleMaintenance,
  onAction,
}: {
  machine: Machine;
  detail: RawFleetHost | null;
  checking: boolean;
  checkResult: CheckOutcome | null;
  onRunCheck: () => void;
  onToggleMaintenance: () => void;
  onAction: (action: 'reboot') => void;
}) {
  const meta = STATUS_META[machine.status];
  const defcon = detail?.defcon ?? null;
  const defconTone =
    defcon === null ? 'text-white' : defcon <= 2 ? 'text-rose-400' : defcon === 3 ? 'text-amber-400' : 'text-emerald-400';

  const ghostBtn =
    'flex items-center gap-2 rounded-xl border border-slate-700 bg-slate-800 px-4 py-2.5 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-700 hover:text-white';

  return (
    <section className="rounded-2xl border border-white/5 bg-slate-800/20">
      <header className="flex items-center justify-between gap-3 border-b border-white/5 bg-white/[0.02] px-5 py-3">
        <div className="flex items-center gap-2.5">
          <Activity className="h-4 w-4 text-indigo-400" />
          <h3 className="font-display text-base font-semibold text-white">מצב המכונה</h3>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-600">Live Status</span>
      </header>

      <div className="space-y-5 px-5 py-5">
        <div className="flex flex-wrap items-start gap-x-8 gap-y-5">
          {/* The big readout: LED + state, like an instrument face. */}
          <div className="flex items-center gap-3.5">
            <span className={`relative h-3.5 w-3.5 rounded-full ${meta.dot} ${meta.dotExtra} ${meta.glow}`} />
            <div>
              <div className="font-display text-2xl font-bold leading-none text-white">{meta.label}</div>
              <div className="mt-1.5 font-mono text-[11px] text-slate-500" dir="ltr">
                {machine.id}
              </div>
            </div>
          </div>

          <div className="grid min-w-0 flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
            {machine.kind === 'local' ? (
              <>
                <ReadoutTile label="DEFCON" value={defcon ?? '—'} tone={defconTone} />
                <ReadoutTile label="בעיות שנמצאו" value={detail?.problems_found ?? '—'} />
                <ReadoutTile label="CPU" value={`${machine.cpuUsage}%`} bar={machine.cpuUsage} />
                <ReadoutTile label="זיכרון" value={`${machine.memoryUsage}%`} bar={machine.memoryUsage} />
              </>
            ) : (
              <>
                <ReadoutTile label="Instance" value={machine.ip || '—'} mono />
                <ReadoutTile label="Region" value={detail?.region ?? '—'} mono />
                <ReadoutTile label="נראתה לפני" value={machine.lastPing} />
                <ReadoutTile label="חיבור" value={detail?.enabled === false ? 'מושבת' : 'מאופשר'} tone={detail?.enabled === false ? 'text-slate-500' : 'text-emerald-400'} />
              </>
            )}
          </div>
        </div>

        {detail?.last_error ? (
          <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-xs text-rose-300">
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 break-all" dir="ltr">{detail.last_error}</span>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onRunCheck}
            disabled={checking}
            className="flex items-center gap-2 rounded-xl bg-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 ring-1 ring-inset ring-white/15 transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {checking ? <Loader2 className="h-4 w-4 animate-spin" /> : <Activity className="h-4 w-4" />}
            {checking ? 'מריץ בדיקה…' : machine.kind === 'local' ? 'הרץ סריקה עכשיו' : 'הרץ בדיקה עכשיו'}
          </button>
          <button
            type="button"
            onClick={onToggleMaintenance}
            className={
              machine.maintenanceMode
                ? 'flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/20 px-4 py-2.5 text-sm font-medium text-amber-400 shadow-[0_0_10px_rgba(245,158,11,0.2)] transition-colors hover:bg-amber-500/30'
                : ghostBtn
            }
          >
            <Wrench className="h-4 w-4" />
            {machine.maintenanceMode ? 'בטל מצב תחזוקה' : 'מצב תחזוקה'}
          </button>
          <button
            type="button"
            onClick={() => onAction('reboot')}
            className="flex items-center gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 px-4 py-2.5 text-sm font-medium text-rose-400 transition-colors hover:bg-rose-500/20"
          >
            <Power className="h-4 w-4" />
            הפעלה מחדש
          </button>
        </div>

        {checkResult ? (
          <div
            className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${
              checkResult.ok
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                : 'border-rose-500/30 bg-rose-500/10 text-rose-300'
            }`}
          >
            {checkResult.ok ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span className="min-w-0 flex-1">{checkResult.message}</span>
            <span className="shrink-0 font-mono text-xs text-slate-500" dir="ltr">{checkResult.at}</span>
          </div>
        ) : null}

        <p className="text-xs leading-relaxed text-slate-600">
          מצב תחזוקה משהה את התיקון האוטומטי על מכונה זו בלבד (דגל, ללא reboot). הפעלה מחדש
          נשלחת לתור האישורים ומחכה לאישור מפעיל.
        </p>
      </div>
    </section>
  );
}

function ReadoutTile({
  label,
  value,
  bar,
  tone,
  mono,
}: {
  label: string;
  value: string | number;
  bar?: number;
  tone?: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/5 bg-slate-950/40 px-3.5 py-3">
      <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</div>
      <div
        className={`mt-1 truncate ${mono ? 'font-mono text-sm' : 'font-display text-xl font-semibold'} tabular-nums ${tone ?? 'text-white'}`}
        dir={mono ? 'ltr' : undefined}
        title={String(value)}
      >
        {value}
      </div>
      {typeof bar === 'number' ? (
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-slate-700/50">
          <div
            className={`h-full rounded-full transition-[width] duration-500 ${
              bar >= 90 ? 'bg-rose-400' : bar >= 70 ? 'bg-amber-400' : 'bg-emerald-400'
            }`}
            style={{width: `${Math.min(100, bar)}%`}}
          />
        </div>
      ) : null}
    </div>
  );
}

// --- Area 2: checks configuration ---------------------------------------------

function ChecksPanel({
  loading,
  error,
  groups,
  selection,
  enabledCount,
  view,
  setView,
  query,
  setQuery,
  onToggleTest,
  onSetParam,
  onSetInterval,
}: {
  loading: boolean;
  error: string;
  groups: ReturnType<typeof groupByDomain>;
  selection: Record<string, TestState>;
  enabledCount: number;
  view: 'active' | 'catalog';
  setView: (v: 'active' | 'catalog') => void;
  query: string;
  setQuery: (q: string) => void;
  onToggleTest: (name: string, enabled: boolean) => void;
  onSetParam: (name: string, param: string, value: number) => void;
  onSetInterval: (name: string, value: number) => void;
}) {
  const tabBtn = (active: boolean) =>
    `flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors ${
      active ? 'bg-indigo-500/15 text-white' : 'text-slate-400 hover:text-slate-200'
    }`;

  return (
    <section className="rounded-2xl border border-white/5 bg-slate-800/20">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 bg-white/[0.02] px-5 py-3">
        <div className="flex items-center gap-2.5">
          <ListChecks className="h-4 w-4 text-indigo-400" />
          <h3 className="font-display text-base font-semibold text-white">הבדיקות של המכונה</h3>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-600">Checks Config</span>
      </header>

      {error ? (
        <div className="m-5 flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          <XCircle className="h-4 w-4 shrink-0" />
          טעינת תצורת הבדיקות נכשלה: {error}
        </div>
      ) : loading ? (
        <div className="flex flex-col items-center justify-center gap-3 py-14 text-slate-500">
          <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
          <span className="text-sm">טוען את תצורת הבדיקות…</span>
        </div>
      ) : (
        <div className="space-y-4 px-5 py-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex rounded-xl border border-white/10 bg-slate-950/40 p-1">
              <button type="button" onClick={() => setView('active')} className={tabBtn(view === 'active')}>
                בדיקות פעילות
                <span className="rounded-md bg-indigo-500/20 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-indigo-300">
                  {enabledCount}
                </span>
              </button>
              <button type="button" onClick={() => setView('catalog')} className={tabBtn(view === 'catalog')}>
                <Plus className="h-3.5 w-3.5" />
                הוספה מהקטלוג
              </button>
            </div>
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={view === 'active' ? 'חיפוש בבדיקות של המכונה…' : 'חיפוש בכל הקטלוג…'}
                className="w-full rounded-xl border border-white/10 bg-slate-800/40 py-2 pl-3 pr-11 text-sm text-slate-100 placeholder:text-slate-600 transition-colors focus:border-indigo-400/70 focus:bg-slate-800/70 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
              />
            </div>
          </div>

          {groups.length === 0 ? (
            view === 'active' && !query ? (
              <div className="flex flex-col items-center gap-4 rounded-2xl border border-dashed border-white/10 bg-slate-950/30 px-6 py-10 text-center">
                <ListChecks className="h-8 w-8 text-slate-600" />
                <div className="space-y-1">
                  <div className="font-display text-sm font-semibold text-slate-300">
                    עדיין לא הוגדרו בדיקות למכונה זו
                  </div>
                  <div className="text-xs text-slate-500">
                    הוסף בדיקות מהקטלוג כדי שהסוכן ידע מה לנטר על המכונה.
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setView('catalog')}
                  className="flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 ring-1 ring-inset ring-white/15 transition-colors hover:bg-indigo-400"
                >
                  <Plus className="h-4 w-4" />
                  לקטלוג הבדיקות
                </button>
              </div>
            ) : (
              <div className="py-10 text-center text-sm text-slate-500">לא נמצאו בדיקות תואמות.</div>
            )
          ) : (
            <div className="max-h-[46vh] space-y-3 overflow-y-auto pl-1">
              {groups.map((g) => {
                const groupEnabled = g.tests.filter((t) => selection[t.name]?.enabled).length;
                return (
                  <section key={g.domain.key} className="overflow-hidden rounded-2xl border border-white/5 bg-slate-900/40">
                    <header className="flex items-center justify-between border-b border-white/5 bg-white/[0.02] px-4 py-2.5">
                      <span className="font-display text-sm font-semibold text-white">{g.domain.label}</span>
                      <span className="font-mono text-[11px] tabular-nums text-slate-500">
                        {groupEnabled}/{g.tests.length}
                      </span>
                    </header>
                    <div className="divide-y divide-white/5">
                      {g.tests.map((t) => (
                        <Fragment key={t.name}>
                          <TestRow
                            mod={t}
                            state={selection[t.name]}
                            onToggle={(en: boolean) => onToggleTest(t.name, en)}
                            onSetParam={(param: string, value: number) => onSetParam(t.name, param, value)}
                            onSetInterval={(v: number) => onSetInterval(t.name, v)}
                          />
                        </Fragment>
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// --- Area 3: predictions (scoped to this machine) -----------------------------

function eta(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) return 'לא נצפה';
  if (seconds < 3600) return `${Math.round(seconds / 60)} דק׳`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} שעות`;
  return `${Math.round(seconds / 86400)} ימים`;
}

function PredictionsPanel({host}: {host: string}) {
  const {data, loading, refetch} = usePolling((signal) => getPredictions(host, signal), 30000);
  const predictions = useMemo(() => data?.predictions || [], [data]);

  return (
    <section className="rounded-2xl border border-white/5 bg-slate-800/20">
      <header className="flex items-center justify-between gap-3 border-b border-white/5 bg-white/[0.02] px-5 py-3">
        <div className="flex items-center gap-2.5">
          <BrainCircuit className="h-4 w-4 text-indigo-400" />
          <h3 className="font-display text-base font-semibold text-white">תחזית הסוכן למכונה זו</h3>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden font-mono text-[10px] uppercase tracking-[0.2em] text-slate-600 sm:inline">
            Predictions
          </span>
          <button
            type="button"
            onClick={refetch}
            aria-label="רענון תחזיות"
            className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-300 transition-colors hover:bg-slate-700"
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>

      <div className="px-5 py-5">
        <p className="mb-4 text-xs leading-relaxed text-slate-500">
          תחזיות חציית ספים ממאגר סדרות-הזמן של הסוכן, לפי המדדים שנאספו ממכונה זו.
        </p>
        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-10 text-slate-500">
            <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
            <span className="text-sm">טוען תחזיות…</span>
          </div>
        ) : predictions.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 bg-slate-950/30 px-6 py-10 text-center text-sm text-slate-500">
            {data?.note || 'אין נתוני תחזית עדיין למכונה זו.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {predictions.map((p, idx) => (
              <div
                key={`${p.metric || 'metric'}-${idx}`}
                className="rounded-2xl border border-white/5 bg-slate-950/40 p-5"
              >
                <div className="mb-4 flex items-center justify-between">
                  <div className="rounded-lg bg-indigo-500/10 p-2.5">
                    <BrainCircuit className="h-4 w-4 text-indigo-400" />
                  </div>
                  <span
                    className={`rounded border px-2 py-1 text-xs ${
                      p.will_cross
                        ? 'border-rose-500/20 bg-rose-500/10 text-rose-300'
                        : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
                    }`}
                  >
                    {p.will_cross ? 'סיכון' : 'יציב'}
                  </span>
                </div>
                <h4 className="font-display text-sm font-semibold text-white" dir="ltr">
                  {p.metric || 'metric'}
                </h4>
                <div className="mt-3 space-y-1.5 text-sm text-slate-400">
                  <div className="flex justify-between">
                    <span>נוכחי</span>
                    <span dir="ltr">{String(p.current ?? '-')}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>סף</span>
                    <span dir="ltr">{String(p.threshold ?? '-')}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>זמן משוער</span>
                    <span dir="ltr">{eta(p.eta_seconds)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// --- Area 4: evidence (scoped to this machine) --------------------------------

function EvidencePanel({host, kind}: {host: string; kind: Machine['kind']}) {
  const card =
    'flex items-start gap-4 rounded-2xl border border-white/5 bg-slate-950/40 p-5 transition-colors hover:border-indigo-500/40';
  return (
    <section className="rounded-2xl border border-white/5 bg-slate-800/20">
      <header className="flex items-center justify-between gap-3 border-b border-white/5 bg-white/[0.02] px-5 py-3">
        <div className="flex items-center gap-2.5">
          <FileArchive className="h-4 w-4 text-indigo-400" />
          <h3 className="font-display text-base font-semibold text-white">ראיות המכונה</h3>
        </div>
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-600">Evidence</span>
      </header>

      <div className="space-y-3 px-5 py-5">
        <p className="text-xs leading-relaxed text-slate-500">
          הורדת ראיות חתומות של תיקונים ואודיט, מסוננות למכונה זו.
        </p>

        <a href={evidenceExportUrl(24, 'json', host)} target="_blank" rel="noopener noreferrer" className={card}>
          <FileArchive className="mt-0.5 h-6 w-6 shrink-0 text-indigo-400" />
          <div className="min-w-0">
            <h4 className="font-display text-sm font-semibold text-white">חבילת JSON מוטמעת</h4>
            <p className="mt-1 text-xs text-slate-400">ראיות אודיט ותיקונים מ-24 השעות האחרונות.</p>
          </div>
        </a>

        <a href={evidenceExportUrl(24, 'file', host)} target="_blank" rel="noopener noreferrer" className={card}>
          <Download className="mt-0.5 h-6 w-6 shrink-0 text-emerald-400" />
          <div className="min-w-0">
            <h4 className="font-display text-sm font-semibold text-white">כתיבת חבילה בשרת</h4>
            <p className="mt-1 text-xs text-slate-400">יוצר קובץ ראיות תחת תיקיית ה-state של הסוכן.</p>
          </div>
        </a>

        <a href={logDownloadUrl('monitor', host)} target="_blank" rel="noopener noreferrer" className={card}>
          <Download className="mt-0.5 h-6 w-6 shrink-0 text-slate-300" />
          <div className="min-w-0">
            <h4 className="font-display text-sm font-semibold text-white">הורדת יומן ניטור</h4>
            <p className="mt-1 text-xs text-slate-400">הורדת יומן האודיט של הניטור כאשר הוא קיים.</p>
          </div>
        </a>

        {kind !== 'local' ? (
          <p className="text-[11px] leading-relaxed text-slate-600">
            הראיות והיומנים מאוחסנים אצל הסוכן המקומי ומשקפים את הפעולות שביצע עבור מכונה זו.
          </p>
        ) : null}
      </div>
    </section>
  );
}
