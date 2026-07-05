// Shared building blocks for the checks catalog — used by both the
// add-machine wizard (initial setup) and the machine detail modal (editing an
// existing machine's checks). Extracted from AddMachineWizard so the two stay
// visually and behaviorally identical.

import {Fragment, useEffect, useState} from 'react';
import type {RawModuleInfo, RawModuleParam} from '../services/agentTypes';
import {listModules, listMonitorChecks} from '../services/endpoints';

// FreeSWITCH is represented in the catalog by three code-organisation bundles;
// we hide them and show the real FS-01..40 checks (from /api/checks/freeswitch)
// instead, so operators pick actual tests, not opaque "v2 part 2" entries.
export const FS_BUNDLE_NAMES = new Set(['freeswitch_health', 'freeswitch_v2', 'freeswitch_v2_part2', 'fs_inode_check']);

// --- Domain grouping ---------------------------------------------------------
// The catalog is a flat list of monitors; operators think in "modules" (a
// domain) each holding a "set of tests". We derive the domain from each
// monitor's tags — first matching domain wins, order = priority.
export interface Domain {
  key: string;
  label: string;
  tags: string[];
}

export const DOMAINS: Domain[] = [
  {key: 'freeswitch', label: 'FreeSWITCH', tags: ['freeswitch', 'fs']},
  {key: 'postgres', label: 'Postgres', tags: ['postgres', 'pg']},
  {key: 'disk', label: 'דיסק ואחסון', tags: ['disk', 'storage', 'hardware']},
  {key: 'memory', label: 'זיכרון', tags: ['memory', 'oom', 'cache']},
  {key: 'compute', label: 'מעבד ותהליכים', tags: ['cpu', 'process', 'processes', 'performance', 'ulimit']},
  {key: 'network', label: 'רשת וקישוריות', tags: ['network', 'egress', 'dns', 'http', 'healthz', 'probe']},
  {key: 'security', label: 'אבטחה', tags: ['security', 'integrity', 'tls']},
  {key: 'backup', label: 'גיבוי ו-DR', tags: ['backup', 'dr']},
  {key: 'system', label: 'מערכת וזמן', tags: ['ntp', 'time', 'systemd', 'services', 'kernel', 'cron', 'aws', 'cloud']},
  {key: 'logs', label: 'לוגים', tags: ['logs']},
  {key: 'self', label: 'ניטור עצמי', tags: ['self', 'agent', 'heartbeat']},
];
export const OTHER_DOMAIN: Domain = {key: 'other', label: 'אחר', tags: []};

export function domainFor(mod: RawModuleInfo): Domain {
  for (const d of DOMAINS) {
    if (mod.tags.some((t) => d.tags.includes(t))) return d;
  }
  return OTHER_DOMAIN;
}

export interface DomainGroup {
  domain: Domain;
  tests: RawModuleInfo[];
}

// Group monitors into domain "modules", ordered by the DOMAINS list.
export function groupByDomain(modules: RawModuleInfo[]): DomainGroup[] {
  const order = new Map(DOMAINS.map((d, i) => [d.key, i]));
  const byDomain = new Map<string, DomainGroup>();
  for (const m of modules) {
    const d = domainFor(m);
    if (!byDomain.has(d.key)) byDomain.set(d.key, {domain: d, tests: []});
    byDomain.get(d.key)!.tests.push(m);
  }
  return Array.from(byDomain.values())
    .map((g) => ({...g, tests: g.tests.sort((a, b) => a.name.localeCompare(b.name))}))
    .sort((a, b) => (order.get(a.domain.key) ?? 99) - (order.get(b.domain.key) ?? 99));
}

export const RISK_LABEL: Record<string, string> = {low: 'נמוך', medium: 'בינוני', high: 'גבוה'};
export const RISK_CLASS: Record<string, string> = {
  low: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  medium: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  high: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
};

export const isNumericParam = (p: RawModuleParam) => p.type === 'int' || p.type === 'float';

export const DEFAULT_INTERVAL = 60;

// Per-test working state shared by the wizard and the detail modal.
export interface TestState {
  enabled: boolean;
  interval: number; // how often the check runs (seconds) — universal knob
  params: Record<string, number>; // numeric param name -> value (excl. interval_sec)
}

// Catalog defaults for one monitor: interval + numeric params.
export function defaultTestState(mod: RawModuleInfo, enabled: boolean): TestState {
  const params: Record<string, number> = {};
  let interval = DEFAULT_INTERVAL;
  for (const p of mod.params) {
    if (!isNumericParam(p) || typeof p.default !== 'number') continue;
    if (p.name === 'interval_sec') interval = p.default;
    else params[p.name] = p.default;
  }
  return {enabled, interval, params};
}

// Fetch the full monitor catalog: base monitors (minus the hidden FreeSWITCH
// bundles) plus the real FS-01..40 sub-checks.
export async function loadMonitorCatalog(): Promise<RawModuleInfo[]> {
  const [all, fsChecks] = await Promise.all([listModules(), listMonitorChecks('freeswitch')]);
  const base = all.filter(
    (m) => m.kind === 'monitor' && !m.catalog_only && !FS_BUNDLE_NAMES.has(m.name),
  );
  return [...base, ...fsChecks];
}

// Derive a sensible slider range for a numeric knob from its name/type/default.
// The catalog doesn't ship min/max, so we infer them from the param semantics.
export function knobRange(name: string, type: string, def: number): {min: number; max: number; step: number; unit: string} {
  const n = name.toLowerCase();
  if (n.includes('pct') || n.includes('percent')) return {min: 0, max: 100, step: 1, unit: '%'};
  if (n.includes('load')) return {min: 0.5, max: 16, step: 0.5, unit: ''};
  if (n.includes('day')) return {min: 1, max: 180, step: 1, unit: ''};
  if (n.includes('hour')) return {min: 1, max: 168, step: 1, unit: ''};
  if (n.includes('ms')) return {min: 0, max: Math.max(2000, def * 4), step: 50, unit: ''};
  if (n === 'interval' || n.includes('sec')) return {min: 15, max: 600, step: 15, unit: ''};
  if (n.includes('count')) return {min: 0, max: 50, step: 1, unit: ''};
  if (type === 'float') return {min: 0, max: Math.max(10, def * 4), step: 0.5, unit: ''};
  return {min: 0, max: Math.max(100, def * 4), step: 1, unit: ''};
}

// A fader with a live, directly-editable numeric readout — dragging and
// typing both work. The typed draft is kept as local text state (not derived
// straight from the number) so a mid-edit keystroke never gets clobbered by
// a re-render; it's only parsed and clamped on blur/Enter. The slider's own
// max is a UI heuristic (not a real limit) except for true percentages, so
// typing past it stretches the fader instead of silently capping the value.
export function MiniSlider({label, value, min, max, step, unit, onChange}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  const commit = () => {
    const n = Number(draft);
    if (draft.trim() === '' || Number.isNaN(n)) {
      setDraft(String(value));
      return;
    }
    let next = Math.max(min, n);
    if (unit === '%') next = Math.min(next, 100);
    onChange(next);
    setDraft(String(next));
  };

  const draftNum = Number(draft);
  const effectiveMax = unit === '%' ? max : Math.max(max, Number.isFinite(draftNum) ? draftNum : value);

  return (
    <div className="w-40 shrink-0">
      <div className="mb-1.5 flex items-baseline justify-between gap-1">
        <span className="font-mono text-[10px] tracking-tight text-slate-500" dir="ltr">{label}</span>
        <span className="flex items-baseline gap-0.5">
          <input
            type="number"
            inputMode="decimal"
            min={min}
            max={unit === '%' ? 100 : undefined}
            step={step}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') e.currentTarget.blur();
            }}
            className="numeric-field w-14 rounded border border-transparent bg-transparent px-1 py-0.5 text-left font-display text-xs font-semibold tabular-nums text-indigo-300 transition-colors hover:bg-white/5 focus:border-indigo-400/50 focus:bg-slate-900/70 focus:outline-none"
            dir="ltr"
          />
          {unit ? <span className="font-display text-xs font-semibold text-indigo-300/70">{unit}</span> : null}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={effectiveMax}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="fader w-full"
      />
    </div>
  );
}

export function TestRow({
  mod,
  state,
  domainLabel,
  onToggle,
  onSetParam,
  onSetInterval,
}: {
  mod: RawModuleInfo;
  state: TestState | undefined;
  domainLabel?: string;
  onToggle: (enabled: boolean) => void;
  onSetParam: (param: string, value: number) => void;
  onSetInterval: (value: number) => void;
}) {
  const enabled = state?.enabled ?? false;
  const numericParams = mod.params.filter((p) => isNumericParam(p) && p.name !== 'interval_sec');
  const intervalRange = knobRange('interval', 'int', DEFAULT_INTERVAL);
  const showRisk = mod.risk === 'medium' || mod.risk === 'high';
  return (
    <div className={`px-4 py-3 transition-opacity ${enabled ? '' : 'opacity-55'}`}>
      <label className="flex min-w-0 cursor-pointer items-start gap-3">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onToggle(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded accent-indigo-500"
        />
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            {domainLabel ? (
              <span className="rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-400">{domainLabel}</span>
            ) : null}
            {mod.liveness ? (
              <span className="wizard-pulse h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" title="בדיקת ליבה" />
            ) : null}
            <span className="text-sm font-medium text-slate-100">{mod.title_he || mod.name}</span>
            {showRisk ? (
              <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${RISK_CLASS[mod.risk] ?? RISK_CLASS.low}`}>
                סיכון {RISK_LABEL[mod.risk] ?? mod.risk}
              </span>
            ) : null}
          </span>
          <span className="mt-0.5 block font-mono text-[11px] text-slate-600" dir="ltr">
            {mod.name}
          </span>
        </span>
      </label>
      {/* Every enabled test is configurable: its thresholds (if any) plus a
          universal run-interval slider — so tests with no params (e.g. most
          FreeSWITCH checks) are still tunable, not select-only. */}
      {enabled ? (
        <div className="mr-7 mt-3 flex flex-wrap items-start gap-x-6 gap-y-3">
          {numericParams.map((p) => {
            const r = knobRange(p.name, p.type, typeof p.default === 'number' ? p.default : 0);
            return (
              <Fragment key={p.name}>
                <MiniSlider
                  label={p.name}
                  value={state?.params[p.name] ?? (typeof p.default === 'number' ? p.default : r.min)}
                  min={r.min}
                  max={r.max}
                  step={r.step}
                  unit={r.unit}
                  onChange={(v: number) => onSetParam(p.name, v)}
                />
              </Fragment>
            );
          })}
          <MiniSlider
            label="interval_sec"
            value={state?.interval ?? DEFAULT_INTERVAL}
            min={intervalRange.min}
            max={intervalRange.max}
            step={intervalRange.step}
            unit={intervalRange.unit}
            onChange={(v) => onSetInterval(v)}
          />
        </div>
      ) : null}
    </div>
  );
}
