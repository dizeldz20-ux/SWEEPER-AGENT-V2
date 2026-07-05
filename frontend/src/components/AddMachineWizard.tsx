import {Fragment, useEffect, useMemo, useState} from 'react';
import {
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Cloud,
  Loader2,
  Search,
  Server,
  XCircle,
} from 'lucide-react';
import {Modal} from './Modal';
import {createConnector, saveHostConfig, testConnector} from '../services/endpoints';
import type {RawModuleInfo, RawModuleParam} from '../services/agentTypes';
import {
  DEFAULT_INTERVAL,
  MiniSlider,
  TestRow,
  defaultTestState,
  groupByDomain,
  isNumericParam,
  knobRange,
  loadMonitorCatalog,
  type DomainGroup,
  type TestState,
} from './checksCatalog';

// --- Wizard state ------------------------------------------------------------
type Phase = 'form' | 'catalog' | 'saving' | 'result';

interface SaveResult {
  connectorCreated: boolean;
  configSaved: boolean;
  connected: boolean;
  error?: string;
}

const NAME_RE = /^[a-zA-Z0-9._-]+$/;

interface AddMachineWizardProps {
  open: boolean;
  onClose: () => void;
  onSaved: () => void; // refresh the connector list in the parent
}

export function AddMachineWizard({open, onClose, onSaved}: AddMachineWizardProps) {
  const [phase, setPhase] = useState<Phase>('form');
  const [form, setForm] = useState({name: '', instance_id: '', region: 'il-central-1', description: '', freeswitch_enabled: false});
  const [formError, setFormError] = useState('');

  const [modules, setModules] = useState<RawModuleInfo[]>([]);
  const [modulesError, setModulesError] = useState('');
  const [selection, setSelection] = useState<Record<string, TestState>>({});
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SaveResult | null>(null);

  // Reset everything each time the wizard is (re)opened.
  useEffect(() => {
    if (!open) return;
    setPhase('form');
    setForm({name: '', instance_id: '', region: 'il-central-1', description: '', freeswitch_enabled: false});
    setFormError('');
    setQuery('');
    setResult(null);
  }, [open]);

  // Load the module catalog (monitors only — those are the "tests" the agent
  // runs to alert us) the first time the wizard opens.
  useEffect(() => {
    if (!open || modules.length > 0) return;
    void loadMonitorCatalog()
      .then((monitors) => {
        setModules(monitors);
        // Seed default selection: enable everything except high-risk (operator
        // opts in), with each threshold pre-filled from the catalog default.
        const seed: Record<string, TestState> = {};
        for (const m of monitors) seed[m.name] = defaultTestState(m, m.risk !== 'high');
        setSelection(seed);
      })
      .catch((err) => setModulesError(err instanceof Error ? err.message : String(err)));
  }, [open, modules.length]);

  // Group monitors into domain "modules" (tabs), ordered by the DOMAINS list.
  const allGroups = useMemo(() => groupByDomain(modules), [modules]);

  const selectedCount = useMemo(
    () => modules.filter((m) => selection[m.name]?.enabled).length,
    [modules, selection],
  );

  // --- Selection mutations ---------------------------------------------------
  const setTestEnabled = (name: string, enabled: boolean) =>
    setSelection((prev) => ({...prev, [name]: {...prev[name], enabled}}));

  const setModuleEnabled = (tests: RawModuleInfo[], enabled: boolean) =>
    setSelection((prev) => {
      const next = {...prev};
      for (const t of tests) next[t.name] = {...next[t.name], enabled};
      return next;
    });

  const setParam = (name: string, param: string, value: number) =>
    setSelection((prev) => ({
      ...prev,
      [name]: {...prev[name], params: {...prev[name].params, [param]: value}},
    }));

  const setTestInterval = (name: string, interval: number) =>
    setSelection((prev) => ({...prev, [name]: {...prev[name], interval}}));

  // --- Navigation ------------------------------------------------------------
  const goToCatalog = () => {
    const name = form.name.trim();
    if (!name || !form.instance_id.trim()) {
      setFormError('יש למלא שם מכונה ומזהה Instance.');
      return;
    }
    if (!NAME_RE.test(name)) {
      setFormError('שם המכונה יכול להכיל רק אותיות באנגלית, ספרות, נקודה, מקף וקו תחתון.');
      return;
    }
    setFormError('');
    setPhase('catalog');
  };

  // --- Save: create connector -> persist config -> real SSM connection test --
  const save = async () => {
    setPhase('saving');
    const name = form.name.trim();
    const res: SaveResult = {connectorCreated: false, configSaved: false, connected: false};
    try {
      await createConnector({
        name,
        instance_id: form.instance_id.trim(),
        region: form.region.trim() || 'il-central-1',
        enabled: true,
        freeswitch_enabled: form.freeswitch_enabled,
        tags: {},
      });
      res.connectorCreated = true;
    } catch (err) {
      // Duplicate name (409) or validation error — bounce back to the form.
      setFormError(
        err instanceof Error && /409/.test(err.message)
          ? `כבר קיימת מכונה בשם "${name}".`
          : err instanceof Error
            ? err.message
            : String(err),
      );
      setPhase('form');
      return;
    }

    // Persist the catalog selection + per-test thresholds.
    try {
      const monitors = modules.map((m) => {
        const st = selection[m.name];
        return {
          name: m.name,
          enabled: st?.enabled ?? false,
          interval_sec: st?.interval ?? DEFAULT_INTERVAL,
          ...(st?.params ?? {}),
        };
      });
      await saveHostConfig(name, {description: form.description.trim(), enabled: true, monitors});
      res.configSaved = true;
    } catch (err) {
      res.error = err instanceof Error ? err.message : String(err);
    }

    // Real connection: trigger a single SSM collection so the operator gets
    // immediate proof the machine is reachable.
    try {
      const test = await testConnector(name);
      res.connected = !!test.ok;
      if (!test.ok && test.error) res.error = test.error;
    } catch (err) {
      res.connected = false;
      res.error = err instanceof Error ? err.message : String(err);
    }

    setResult(res);
    setPhase('result');
    onSaved();
  };

  // --- Footer per phase ------------------------------------------------------
  const ghostBtn = 'flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-200';
  const primaryBtn = 'flex items-center gap-2 rounded-xl bg-indigo-500 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 ring-1 ring-inset ring-white/15 transition-colors hover:bg-indigo-400';
  const footer = (() => {
    if (phase === 'form') {
      return (
        <div className="flex items-center justify-between">
          <button type="button" onClick={onClose} className={ghostBtn}>ביטול</button>
          <button type="button" onClick={goToCatalog} className={primaryBtn}>
            המשך לקטלוג
            <ChevronLeft className="h-4 w-4" />
          </button>
        </div>
      );
    }
    if (phase === 'catalog') {
      return (
        <div className="flex items-center justify-between">
          <button type="button" onClick={() => setPhase('form')} className={ghostBtn}>
            <ChevronRight className="h-4 w-4" />
            חזרה לפרטים
          </button>
          <div className="flex items-center gap-4">
            <span className="text-sm text-slate-400">
              <span className="font-display font-semibold tabular-nums text-indigo-300">{selectedCount}</span> בדיקות פעילות
            </span>
            <button
              type="button"
              onClick={() => void save()}
              className="flex items-center gap-2 rounded-xl bg-emerald-500 px-6 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-emerald-500/25 ring-1 ring-inset ring-white/20 transition-colors hover:bg-emerald-400"
            >
              <Cloud className="h-4 w-4" />
              שמור והתחבר
            </button>
          </div>
        </div>
      );
    }
    if (phase === 'result') {
      return (
        <div className="flex items-center justify-end">
          <button type="button" onClick={onClose} className={primaryBtn}>סיום</button>
        </div>
      );
    }
    return null; // saving: no footer actions
  })();

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissable={phase !== 'saving'}
      widthClass={phase === 'catalog' ? 'max-w-5xl' : 'max-w-2xl'}
      kicker="הוספת מכונה"
      title={form.name.trim() ? form.name.trim() : 'מכונה חדשה לניטור'}
      subtitle={<Stepper phase={phase} />}
      footer={footer}
    >
      {phase === 'form' && <MachineForm form={form} setForm={setForm} error={formError} />}
      {phase === 'catalog' && (
        <CatalogStep
          allGroups={allGroups}
          selection={selection}
          query={query}
          setQuery={setQuery}
          error={modulesError}
          loading={modules.length === 0 && !modulesError}
          onToggleTest={setTestEnabled}
          onToggleModule={setModuleEnabled}
          onSetParam={setParam}
          onSetInterval={setTestInterval}
        />
      )}
      {phase === 'saving' && <SavingState name={form.name.trim()} />}
      {phase === 'result' && result && <ResultState name={form.name.trim()} result={result} />}
    </Modal>
  );
}

// --- Segmented step indicator ------------------------------------------------
function Stepper({phase}: {phase: Phase}) {
  const idx = phase === 'form' ? 0 : phase === 'catalog' ? 1 : 2;
  const steps = [{n: 1, label: 'פרטי מכונה'}, {n: 2, label: 'קטלוג בדיקות'}];
  return (
    <ol className="flex items-center gap-3">
      {steps.map((s, i) => {
        const state = i < idx ? 'done' : i === idx ? 'active' : 'pending';
        const badge =
          state === 'done'
            ? 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/40'
            : state === 'active'
              ? 'bg-indigo-500 text-white ring-2 ring-indigo-500/30'
              : 'bg-slate-800 text-slate-500 ring-1 ring-white/5';
        const label =
          state === 'active' ? 'text-white' : state === 'done' ? 'text-slate-300' : 'text-slate-600';
        return (
          <Fragment key={s.n}>
            {i > 0 ? <span className={`h-px w-6 ${i <= idx ? 'bg-indigo-400/50' : 'bg-slate-700'}`} /> : null}
            <li className="flex items-center gap-2">
              <span className={`flex h-6 w-6 items-center justify-center rounded-full font-display text-xs font-semibold tabular-nums ${badge}`}>
                {state === 'done' ? <Check className="h-3.5 w-3.5" /> : s.n}
              </span>
              <span className={`text-sm font-medium ${label}`}>{s.label}</span>
            </li>
          </Fragment>
        );
      })}
    </ol>
  );
}

// --- Step 1: machine details -------------------------------------------------
function MachineForm({
  form,
  setForm,
  error,
}: {
  form: {name: string; instance_id: string; region: string; description: string; freeswitch_enabled: boolean};
  setForm: (f: {name: string; instance_id: string; region: string; description: string; freeswitch_enabled: boolean}) => void;
  error: string;
}) {
  const field = 'w-full rounded-xl border border-white/10 bg-slate-800/40 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 transition-colors focus:border-indigo-400/70 focus:bg-slate-800/70 focus:outline-none focus:ring-2 focus:ring-indigo-500/20';
  const label = 'mb-1.5 block font-mono text-[11px] uppercase tracking-[0.14em] text-slate-500';
  return (
    <div className="wizard-rise-in space-y-6">
      <p className="flex items-start gap-2.5 text-sm leading-relaxed text-slate-400">
        <Server className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
        <span>הגדר את זהות המכונה וחיבור ה-AWS SSM. בשלב הבא תרכיב את מערך הבדיקות שהסוכן יריץ עליה.</span>
      </p>
      <div className="grid grid-cols-1 gap-x-5 gap-y-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className={label}>שם המכונה</label>
          <input
            value={form.name}
            onChange={(e) => setForm({...form, name: e.target.value})}
            placeholder="prod-fs-01"
            className={field}
            dir="ltr"
            autoFocus
          />
        </div>
        <div>
          <label className={label}>מזהה Instance</label>
          <input
            value={form.instance_id}
            onChange={(e) => setForm({...form, instance_id: e.target.value})}
            placeholder="i-0abc123def456"
            className={field}
            dir="ltr"
          />
        </div>
        <div>
          <label className={label}>אזור · Region</label>
          <input
            value={form.region}
            onChange={(e) => setForm({...form, region: e.target.value})}
            placeholder="il-central-1"
            className={field}
            dir="ltr"
          />
        </div>
        <div className="sm:col-span-2">
          <label className={label}>תיאור <span className="normal-case text-slate-600">(אופציונלי)</span></label>
          <input
            value={form.description}
            onChange={(e) => setForm({...form, description: e.target.value})}
            placeholder="שרת FreeSWITCH ראשי"
            className={field}
          />
        </div>
        <div className="sm:col-span-2">
          <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-white/10 bg-slate-800/40 px-3.5 py-3 transition-colors hover:bg-slate-800/60">
            <input
              type="checkbox"
              checked={form.freeswitch_enabled}
              onChange={(e) => setForm({...form, freeswitch_enabled: e.target.checked})}
              className="mt-0.5 h-4 w-4 shrink-0 rounded accent-indigo-500"
            />
            <span className="min-w-0">
              <span className="block text-sm font-medium text-slate-200">מכונת FreeSWITCH (מרכזייה)</span>
              <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">
                סמן רק אם מותקנת על המכונה מרכזיית FreeSWITCH. הסוכן יריץ בדיקות FreeSWITCH (תהליך, SIP, רישומים) על המכונה הזו רק כשמסומן. מכונת הסוכן עצמה לעולם אינה נבדקת ל-FreeSWITCH.
              </span>
            </span>
          </label>
        </div>
      </div>
      {error ? (
        <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-sm text-rose-300">
          <XCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}
    </div>
  );
}

// --- Step 2: catalog ---------------------------------------------------------

function CatalogStep({
  allGroups,
  selection,
  query,
  setQuery,
  error,
  loading,
  onToggleTest,
  onToggleModule,
  onSetParam,
  onSetInterval,
}: {
  allGroups: DomainGroup[];
  selection: Record<string, TestState>;
  query: string;
  setQuery: (q: string) => void;
  error: string;
  loading: boolean;
  onToggleTest: (name: string, enabled: boolean) => void;
  onToggleModule: (tests: RawModuleInfo[], enabled: boolean) => void;
  onSetParam: (name: string, param: string, value: number) => void;
  onSetInterval: (name: string, interval: number) => void;
}) {
  const [activeKey, setActiveKey] = useState('');

  // Keep the active tab valid as the catalog loads.
  useEffect(() => {
    if (allGroups.length && !allGroups.some((g) => g.domain.key === activeKey)) {
      setActiveKey(allGroups[0].domain.key);
    }
  }, [allGroups, activeKey]);

  if (error) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
        <XCircle className="h-4 w-4 shrink-0" />
        טעינת הקטלוג נכשלה: {error}
      </div>
    );
  }
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-500">
        <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
        <span className="text-sm">טוען קטלוג בדיקות…</span>
      </div>
    );
  }

  const q = query.trim().toLowerCase();
  const searching = q.length > 0;
  const matchesQ = (m: RawModuleInfo) =>
    m.title_he.toLowerCase().includes(q) ||
    m.name.toLowerCase().includes(q) ||
    m.tags.some((t) => t.toLowerCase().includes(q));
  const searchResults = searching
    ? allGroups.flatMap((g) => g.tests.filter(matchesQ).map((mod) => ({domain: g.domain, mod})))
    : [];
  const activeGroup = allGroups.find((g) => g.domain.key === activeKey) ?? allGroups[0];

  return (
    <div className="wizard-rise-in space-y-4">
      <div className="relative">
        <Search className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="חיפוש בכל הקטלוג…"
          className="w-full rounded-xl border border-white/10 bg-slate-800/40 py-2.5 pr-11 pl-3 text-sm text-slate-100 placeholder:text-slate-600 transition-colors focus:border-indigo-400/70 focus:bg-slate-800/70 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        />
      </div>

      <div className="flex gap-5">
        {/* Topic rail — one entry per catalog module. RTL puts it on the right. */}
        <nav className="w-48 shrink-0 self-start rounded-2xl border border-white/5 bg-slate-950/40 p-1.5">
          {allGroups.map((g) => {
            const en = g.tests.filter((t) => selection[t.name]?.enabled).length;
            const active = !searching && g.domain.key === activeGroup?.domain.key;
            return (
              <Fragment key={g.domain.key}>
                <button
                  type="button"
                  onClick={() => {
                    setQuery('');
                    setActiveKey(g.domain.key);
                  }}
                  className={`relative flex w-full items-center justify-between gap-2 rounded-xl px-3 py-2 text-sm transition-colors ${
                    active ? 'bg-indigo-500/15 text-white' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'
                  }`}
                >
                  {active ? (
                    <span className="absolute inset-y-2 right-0 w-0.5 rounded-full bg-indigo-400" />
                  ) : null}
                  <span className="truncate">{g.domain.label}</span>
                  <span
                    className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${
                      en > 0 ? 'bg-indigo-500/20 text-indigo-300' : 'bg-slate-800/80 text-slate-600'
                    }`}
                  >
                    {en}/{g.tests.length}
                  </span>
                </button>
              </Fragment>
            );
          })}
        </nav>

        {/* Content — search results, or the active tab's module. */}
        <div className="min-w-0 flex-1">
          {searching ? (
            searchResults.length === 0 ? (
              <div className="py-12 text-center text-sm text-slate-500">לא נמצאו בדיקות תואמות.</div>
            ) : (
              <div className="max-h-[56vh] divide-y divide-white/5 overflow-y-auto overflow-x-hidden rounded-2xl border border-white/5 bg-slate-800/20">
                {searchResults.map(({domain, mod}) => (
                  <Fragment key={mod.name}>
                    <TestRow
                      mod={mod}
                      domainLabel={domain.label}
                      state={selection[mod.name]}
                      onToggle={(en: boolean) => onToggleTest(mod.name, en)}
                      onSetParam={(param: string, value: number) => onSetParam(mod.name, param, value)}
                      onSetInterval={(v: number) => onSetInterval(mod.name, v)}
                    />
                  </Fragment>
                ))}
              </div>
            )
          ) : activeGroup ? (
            <DomainPanel
              group={activeGroup}
              selection={selection}
              onToggleTest={onToggleTest}
              onToggleModule={onToggleModule}
              onSetParam={onSetParam}
              onSetInterval={onSetInterval}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function DomainPanel({
  group,
  selection,
  onToggleTest,
  onToggleModule,
  onSetParam,
  onSetInterval,
}: {
  group: DomainGroup;
  selection: Record<string, TestState>;
  onToggleTest: (name: string, enabled: boolean) => void;
  onToggleModule: (tests: RawModuleInfo[], enabled: boolean) => void;
  onSetParam: (name: string, param: string, value: number) => void;
  onSetInterval: (name: string, interval: number) => void;
}) {
  const {domain, tests} = group;
  const enabledCount = tests.filter((t) => selection[t.name]?.enabled).length;
  const allOn = enabledCount === tests.length;

  // Module-level threshold: the numeric param (excl. interval) shared by 2+
  // tests here — usually threshold_pct. Its slider bulk-applies to every test
  // that has it, so the whole module can be tuned in one place; individual
  // tests can still be overridden below.
  const sharedSpec = useMemo(() => {
    const counts = new Map<string, {n: number; spec: RawModuleParam}>();
    for (const t of tests) {
      for (const p of t.params) {
        if (!isNumericParam(p) || p.name === 'interval_sec') continue;
        const cur = counts.get(p.name);
        counts.set(p.name, {n: (cur?.n ?? 0) + 1, spec: cur?.spec ?? p});
      }
    }
    let best: RawModuleParam | null = null;
    let bestN = 1;
    for (const {n, spec} of counts.values()) {
      if (n > bestN) {
        best = spec;
        bestN = n;
      }
    }
    return best;
  }, [tests]);

  const sharedRange = sharedSpec
    ? knobRange(sharedSpec.name, sharedSpec.type, typeof sharedSpec.default === 'number' ? sharedSpec.default : 0)
    : null;

  const sharedValue = useMemo(() => {
    if (!sharedSpec) return 0;
    const vals = new Set<number>();
    for (const t of tests) {
      const v = selection[t.name]?.params[sharedSpec.name];
      if (typeof v === 'number') vals.add(v);
    }
    if (vals.size === 1) return [...vals][0];
    return typeof sharedSpec.default === 'number' ? sharedSpec.default : (sharedRange?.min ?? 0);
  }, [sharedSpec, sharedRange, tests, selection]);

  const applyModuleParam = (value: number) => {
    if (!sharedSpec) return;
    for (const t of tests) {
      if (t.params.some((p) => p.name === sharedSpec.name)) onSetParam(t.name, sharedSpec.name, value);
    }
  };

  const pct = tests.length ? Math.round((enabledCount / tests.length) * 100) : 0;
  return (
    <section className="overflow-hidden rounded-2xl border border-white/5 bg-slate-800/20">
      <header className="space-y-3 border-b border-white/5 bg-white/[0.02] px-4 py-3.5">
        <div className="flex items-center justify-between gap-3">
          <label className="flex cursor-pointer items-center gap-3">
            <input
              type="checkbox"
              checked={allOn}
              ref={(el) => {
                if (el) el.indeterminate = enabledCount > 0 && !allOn;
              }}
              onChange={(e) => onToggleModule(tests, e.target.checked)}
              className="h-4 w-4 rounded accent-indigo-500"
            />
            <span className="font-display text-base font-semibold text-white">{domain.label}</span>
          </label>
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[11px] tabular-nums text-slate-500">{enabledCount}/{tests.length}</span>
            <div className="h-1 w-16 overflow-hidden rounded-full bg-slate-700/50">
              <div className="h-full rounded-full bg-indigo-400 transition-[width] duration-300" style={{width: `${pct}%`}} />
            </div>
          </div>
        </div>
        {sharedSpec && sharedRange ? (
          <div className="flex items-center gap-3 rounded-xl border border-white/5 bg-slate-950/40 px-3 py-2">
            <span className="shrink-0 text-[11px] font-medium text-slate-400">סף לכל המודול</span>
            <MiniSlider
              label={sharedSpec.name}
              value={sharedValue}
              min={sharedRange.min}
              max={sharedRange.max}
              step={sharedRange.step}
              unit={sharedRange.unit}
              onChange={applyModuleParam}
            />
          </div>
        ) : null}
      </header>
      <div className="max-h-[52vh] divide-y divide-white/5 overflow-y-auto">
        {tests.map((t) => (
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
}

// --- Saving + result screens -------------------------------------------------
function SavingState({name}: {name: string}) {
  return (
    <div className="wizard-rise-in flex flex-col items-center justify-center gap-5 py-16 text-center">
      <div className="relative flex h-16 w-16 items-center justify-center">
        <span className="absolute inset-0 rounded-full border border-indigo-500/20" />
        <span className="absolute inset-0 animate-ping rounded-full bg-indigo-500/10" />
        <Loader2 className="h-8 w-8 animate-spin text-indigo-400" />
      </div>
      <div className="space-y-1.5">
        <div className="font-display text-base font-semibold text-white">מתחבר אל המכונה</div>
        <div className="text-sm text-slate-400">
          שומר תצורה ומריץ בדיקת SSM אל <span className="font-mono text-slate-200" dir="ltr">{name}</span>
        </div>
        <div className="text-xs text-slate-600">החיבור הראשוני עשוי להימשך מספר שניות</div>
      </div>
    </div>
  );
}

function ResultState({name, result}: {name: string; result: SaveResult}) {
  const ok = result.connected;
  return (
    <div className="wizard-rise-in flex flex-col items-center justify-center gap-5 py-10 text-center">
      <div
        className={`flex h-16 w-16 items-center justify-center rounded-full ring-1 ${
          ok ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/30' : 'bg-rose-500/10 text-rose-400 ring-rose-500/30'
        }`}
      >
        {ok ? <CheckCircle2 className="h-8 w-8" /> : <XCircle className="h-8 w-8" />}
      </div>
      <div className="space-y-1.5">
        <div className="font-display text-xl font-semibold text-white">
          {ok ? 'המכונה מחוברת' : 'החיבור נכשל'}
        </div>
        <div className="mx-auto max-w-md text-sm leading-relaxed text-slate-400">
          {ok ? (
            <>
              <span className="font-mono text-slate-200" dir="ltr">{name}</span> משיבה דרך SSM. מערך הבדיקות
              שהגדרת נשמר בתצורת המכונה.
            </>
          ) : (
            <>
              התצורה נשמרה עבור <span className="font-mono text-slate-200" dir="ltr">{name}</span>, אך בדיקת
              החיבור הראשונית נכשלה. בדוק את הרשאות ה-IAM/SSM ואת מזהה ה-Instance, ונסה שוב מכרטיס המכונה.
            </>
          )}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2 text-xs">
        <StatusChip ok={result.connectorCreated} label="המכונה נשמרה" />
        <StatusChip ok={result.configSaved} label="התצורה נשמרה" />
        <StatusChip ok={result.connected} label="חיבור SSM" />
      </div>
      {result.error && !ok ? (
        <div className="mx-auto max-w-md rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2.5 text-xs text-rose-300" dir="ltr">
          {result.error}
        </div>
      ) : null}
    </div>
  );
}

function StatusChip({ok, label}: {ok: boolean; label: string}) {
  return (
    <span
      className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
        ok
          ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300'
          : 'border-white/5 bg-slate-800 text-slate-500'
      }`}
    >
      {ok ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
      {label}
    </span>
  );
}
