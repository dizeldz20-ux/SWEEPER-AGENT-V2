import {useEffect, useState, type ReactNode} from 'react';
import {
  BellRing,
  CheckCircle2,
  Cloud,
  Hash,
  Info,
  Plus,
  Send,
  Shield,
  SlidersHorizontal,
  Terminal,
  Trash2,
} from 'lucide-react';
import type {FilterRule} from '../types';
import type {RawBot, RawBots, RawConnector} from '../services/agentTypes';
import {
  deleteBot,
  deleteConnector,
  getFilterRules,
  listBots,
  listConnectors,
  testBot,
  testConnector,
} from '../services/endpoints';
import {AddMachineWizard} from './AddMachineWizard';
import {AddBotWizard} from './AddBotWizard';
import {useData} from '../context/DataContext';

type Platform = 'telegram' | 'slack';

const EMPTY_BOTS: RawBots = {telegram: [], slack: []};

// --- Official brand marks --------------------------------------------------
// Slack (4-color) and Telegram are consumer brands, absent from the AI-focused
// icon set, so we inline their canonical logos (exact brand-page paths) rather
// than a generic placeholder.
function TelegramLogo({className}: {className?: string}) {
  return (
    <svg viewBox="0 0 240 240" className={className} aria-hidden="true">
      <circle cx="120" cy="120" r="120" fill="#29A9EB" />
      <path
        fill="#fff"
        d="M44.7 118.7c34.99-15.24 58.32-25.29 69.98-30.14 33.33-13.86 40.26-16.27 44.77-16.35.99-.02 3.22.23 4.66 1.4 1.22.99 1.56 2.33 1.72 3.27.16.94.36 3.08.2 4.75-1.8 18.96-9.61 64.94-13.58 86.16-1.68 8.98-4.99 11.99-8.19 12.29-6.95.64-12.23-4.59-18.96-9.01-10.53-6.9-16.48-11.2-26.7-17.94-11.81-7.78-4.16-12.06 2.58-19.05 1.76-1.83 32.4-29.7 32.99-32.23.07-.32.14-1.5-.56-2.12-.7-.63-1.73-.41-2.48-.24-1.06.24-17.9 11.37-50.52 33.39-4.78 3.28-9.11 4.88-12.99 4.8-4.28-.09-12.51-2.42-18.63-4.41-7.5-2.44-13.46-3.73-12.94-7.88.27-2.16 3.25-4.37 8.94-6.63z"
      />
    </svg>
  );
}

function SlackLogo({className}: {className?: string}) {
  return (
    <svg viewBox="0 0 122.8 122.8" className={className} aria-hidden="true">
      <path
        fill="#E01E5A"
        d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9zm6.5 0c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z"
      />
      <path
        fill="#36C5F0"
        d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2zm0 6.5c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z"
      />
      <path
        fill="#2EB67D"
        d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2zm-6.5 0c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z"
      />
      <path
        fill="#ECB22E"
        d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9zm0-6.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z"
      />
    </svg>
  );
}

// Per-platform visual identity — fixed class strings (not interpolated) so
// Tailwind's JIT keeps them in the build.
const PLATFORM: Record<
  Platform,
  {title: string; desc: string; logo: ReactNode; glow: string; addBtn: string; badge: string}
> = {
  telegram: {
    title: 'Telegram',
    desc: 'התראות ישירות לצ׳אט או לערוץ',
    logo: <TelegramLogo className="h-7 w-7" />,
    glow: 'bg-sky-500/20',
    addBtn:
      'bg-sky-500/10 text-sky-300 border-sky-500/25 hover:bg-sky-500/20 hover:border-sky-500/40',
    badge: 'bg-sky-500/10 text-sky-300 ring-sky-500/25',
  },
  slack: {
    title: 'Slack',
    desc: 'הודעות לערוצי צוות דרך Slack App',
    logo: <SlackLogo className="h-6 w-6" />,
    glow: 'bg-fuchsia-500/15',
    addBtn:
      'bg-violet-500/10 text-violet-300 border-violet-500/25 hover:bg-violet-500/20 hover:border-violet-500/40',
    badge: 'bg-violet-500/10 text-violet-300 ring-violet-500/25',
  },
};

export function Settings() {
  const {refetch: refetchFleet} = useData();
  const [bots, setBots] = useState<RawBots>(EMPTY_BOTS);
  const [rules, setRules] = useState<FilterRule[]>([]);
  const [rulesNote, setRulesNote] = useState('');
  const [connectors, setConnectors] = useState<RawConnector[]>([]);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [botWizard, setBotWizard] = useState<{open: boolean; platform: Platform}>({
    open: false,
    platform: 'telegram',
  });
  const [message, setMessage] = useState('');

  const load = async () => {
    const [botsData, filterData, connectorData] = await Promise.all([
      listBots(),
      getFilterRules(),
      listConnectors(),
    ]);
    setBots(botsData);
    setRules(filterData.rules || []);
    setRulesNote(filterData.note || '');
    setConnectors(connectorData);
  };

  useEffect(() => {
    void load().catch((err) => setMessage(err instanceof Error ? err.message : String(err)));
  }, []);

  const openAddBot = (platform: Platform) => setBotWizard({open: true, platform});

  const runBotTest = async (id: string) => {
    const r = await testBot(id);
    setMessage(r.ok ? (r.message || 'הודעת הבדיקה נשלחה.') : (r.message || r.error || 'בדיקת הבוט נכשלה.'));
  };

  const removeBot = async (id: string) => {
    await deleteBot(id);
    await load();
  };

  const runConnectorTest = async (name: string) => {
    const result = await testConnector(name);
    setMessage(result.ok ? `המחבר ${name} תקין.` : result.error || `המחבר ${name} נכשל.`);
  };

  const reportErr = (err: unknown) =>
    setMessage(err instanceof Error ? err.message : String(err));

  const totalBots = bots.telegram.length + bots.slack.length;

  return (
    <div className="p-6 lg:p-8 flex flex-col gap-8 view-in max-w-6xl mx-auto" dir="rtl">
      {/* Page header: identity + at-a-glance counters */}
      <header className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-4">
          <div className="icon-tile h-12 w-12 bg-indigo-500/12 text-indigo-300">
            <SlidersHorizontal className="h-6 w-6" />
          </div>
          <div>
            <h2 className="text-[26px] font-extrabold tracking-tight text-white leading-tight">הגדרות סוכן</h2>
            <p className="text-sm text-slate-400 mt-1">
              ערוצי התראה, מכונות מנוטרות וחוקי סינון — במקום אחד.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <StatChip icon={<BellRing className="h-4 w-4 text-indigo-400" />} label="ערוצי התראה" value={totalBots} />
          <StatChip icon={<Cloud className="h-4 w-4 text-sky-400" />} label="מכונות" value={connectors.length} />
        </div>
      </header>

      {message ? (
        <div className="flex items-start gap-2.5 rounded-2xl border border-indigo-500/25 bg-indigo-500/10 px-4 py-3 text-sm text-indigo-200">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-400" />
          <span className="leading-relaxed">{message}</span>
        </div>
      ) : null}

      {/* Section 1 — notification channels */}
      <section className="space-y-4">
        <SectionHeading
          title="ערוצי התראות"
          desc="חבר בוטים כדי לקבל התראות בזמן אמת בפלטפורמות שהצוות שלך עובד בהן."
        />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <BotSection
            cfg={PLATFORM.telegram}
            platform="telegram"
            bots={bots.telegram}
            onAdd={() => openAddBot('telegram')}
            onTest={(id) => void runBotTest(id).catch(reportErr)}
            onRemove={(id) => void removeBot(id).catch(reportErr)}
          />
          <BotSection
            cfg={PLATFORM.slack}
            platform="slack"
            bots={bots.slack}
            onAdd={() => openAddBot('slack')}
            onTest={(id) => void runBotTest(id).catch(reportErr)}
            onRemove={(id) => void removeBot(id).catch(reportErr)}
          />
        </div>
      </section>

      {/* Section 2 — monitoring & configuration */}
      <section className="space-y-4">
        <SectionHeading
          title="ניטור ותצורה"
          desc="המכונות שהסוכן מנטר, והחוקים שמסננים אילו אירועים מגיעים אליך."
        />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
          {/* Monitored machines — the wider, primary card */}
          <div className="lg:col-span-2 flex flex-col panel">
            <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] p-6">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-500/10 ring-1 ring-inset ring-sky-500/25">
                  <Cloud className="h-5 w-5 text-sky-400" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-white leading-tight">מכונות מנוטרות</h3>
                  <p className="text-xs text-slate-400 mt-0.5">{connectors.length} מחוברות</p>
                </div>
              </div>
              <button
                onClick={() => setWizardOpen(true)}
                className="flex items-center gap-2 rounded-xl bg-sky-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-400"
              >
                <Plus className="h-4 w-4" />
                הוסף מכונה
              </button>
            </div>

            <div className="p-6">
              {connectors.length === 0 ? (
                <EmptyState
                  icon={<Cloud className="h-6 w-6 text-slate-500" />}
                  text="עדיין לא הוגדרו מכונות לניטור."
                  action={
                    <button
                      onClick={() => setWizardOpen(true)}
                      className="flex items-center gap-2 rounded-xl border border-sky-500/25 bg-sky-500/10 px-4 py-2 text-sm font-medium text-sky-300 transition-colors hover:bg-sky-500/20"
                    >
                      <Plus className="h-4 w-4" />
                      הוסף מכונה ראשונה
                    </button>
                  }
                />
              ) : (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {connectors.map((connector) => (
                    <div
                      key={connector.name}
                      className="flex flex-col gap-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.035] p-4 transition-colors"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <h4 className="truncate text-sm font-bold text-slate-100">{connector.name}</h4>
                          <div className="mt-2 flex items-center gap-2 font-mono text-xs text-slate-400 num" dir="ltr">
                            <Terminal className="h-3 w-3 shrink-0" />
                            <span className="truncate">{connector.instance_id}</span>
                          </div>
                        </div>
                        <span className="shrink-0 rounded-md bg-white/[0.06] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-300 num">
                          {connector.region}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 border-t border-white/[0.06] pt-4">
                        <button
                          onClick={() => void runConnectorTest(connector.name).catch(reportErr)}
                          className="flex-1 rounded-lg bg-sky-500/10 py-2 text-center text-xs font-medium text-sky-400 transition-colors hover:bg-sky-500/20 hover:text-sky-300"
                        >
                          בדיקת חיבור
                        </button>
                        <button
                          onClick={() => void deleteConnector(connector.name).then(load).catch(reportErr)}
                          className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-rose-400/10 hover:text-rose-400"
                          aria-label="מחק מכונה"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Filter rules — narrow companion card */}
          <div className="flex flex-col panel">
            <div className="flex items-center gap-3 border-b border-white/[0.06] p-6">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-emerald-500/10 ring-1 ring-inset ring-emerald-500/25">
                <Shield className="h-5 w-5 text-emerald-400" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-white leading-tight">חוקי סינון</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  {rules.length > 0 ? `${rules.length} חוקים פעילים` : 'ניהול לפי chat'}
                </p>
              </div>
            </div>
            <div className="p-6">
              {rules.length === 0 ? (
                <p className="text-sm leading-relaxed text-slate-500">
                  {rulesNote || 'לא נאכפים חוקי סינון עדיין. הסוכן פועל כרגע לפי חוקי סף.'}
                </p>
              ) : (
                <div className="space-y-3">
                  {rules.map((rule) => (
                    <div key={rule.id} className="rounded-2xl border border-white/[0.05] bg-white/[0.02] hover:bg-white/[0.04] p-4 transition-colors">
                      <div className="text-sm font-bold text-slate-100">{rule.name}</div>
                      <div className="mt-1.5 font-mono text-xs text-slate-500" dir="ltr">{rule.pattern}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      <AddMachineWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onSaved={() => {
          // Refresh the local connector list AND the global fleet, so the new
          // machine shows up immediately in the "מכונות" view instead of
          // waiting for the fleet poll (~10s).
          refetchFleet();
          void load().catch(reportErr);
        }}
      />

      <AddBotWizard
        open={botWizard.open}
        platform={botWizard.platform}
        onClose={() => setBotWizard((s) => ({...s, open: false}))}
        onSaved={() => void load().catch(reportErr)}
      />
    </div>
  );
}

// A compact "label + number" pill for the header summary row.
function StatChip({icon, label, value}: {icon: ReactNode; label: string; value: number}) {
  return (
    <div className="flex items-center gap-2.5 rounded-2xl border border-white/[0.07] bg-white/[0.03] px-4 py-2.5">
      <div className="icon-tile h-8 w-8 bg-white/[0.04]">{icon}</div>
      <div className="leading-tight">
        <div className="text-lg font-bold text-white num" dir="ltr">{value}</div>
        <div className="text-[11px] text-slate-400">{label}</div>
      </div>
    </div>
  );
}

// A titled section divider with an explanatory line beneath it.
function SectionHeading({title, desc}: {title: string; desc: string}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-3">
        <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-slate-300">{title}</h3>
        <div className="h-px flex-1 bg-gradient-to-l from-transparent via-slate-800 to-slate-800" />
      </div>
      <p className="text-xs text-slate-500">{desc}</p>
    </div>
  );
}

// Shared empty-state block: icon bubble, message, and a call-to-action.
function EmptyState({icon, text, action}: {icon: ReactNode; text: string; action: ReactNode}) {
  return (
    <div className="flex flex-col items-center gap-3 py-10 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-800/60">{icon}</div>
      <div className="text-sm text-slate-400">{text}</div>
      {action}
    </div>
  );
}

// A single platform card: branded header, connected-bot list (or empty state),
// and an add action that always sits at the bottom for a stable layout.
function BotSection({
  cfg,
  platform,
  bots,
  onAdd,
  onTest,
  onRemove,
}: {
  cfg: (typeof PLATFORM)[Platform];
  platform: Platform;
  bots: RawBot[];
  onAdd: () => void;
  onTest: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="relative flex flex-col overflow-hidden panel">
      {/* soft brand glow in the corner */}
      <div className={`pointer-events-none absolute -top-16 -right-12 h-40 w-40 rounded-full blur-3xl ${cfg.glow}`} />

      <div className="relative flex flex-1 flex-col p-6">
        <div className="mb-5 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3.5">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white shadow-lg shadow-black/20 ring-1 ring-black/5">
              {cfg.logo}
            </div>
            <div>
              <h3 className="text-base font-semibold text-white leading-tight">{cfg.title}</h3>
              <p className="mt-0.5 text-xs text-slate-400">{cfg.desc}</p>
            </div>
          </div>
          {bots.length > 0 ? (
            <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-inset ${cfg.badge}`}>
              {bots.length} מחוברים
            </span>
          ) : null}
        </div>

        <div className="flex flex-1 flex-col">
          {bots.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 py-8 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-slate-800/60 opacity-70">
                {cfg.logo}
              </div>
              <div className="text-sm text-slate-400">עדיין לא חוברו בוטים.</div>
            </div>
          ) : (
            <div className="space-y-3">
              {bots.map((bot) => (
                <div
                  key={bot.id}
                  className="flex flex-col gap-3 rounded-2xl border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.035] p-4 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-100">{bot.name}</div>
                      <div className="mt-1.5 flex items-center gap-1.5 font-mono text-xs text-slate-400" dir="ltr">
                        {platform === 'telegram' ? (
                          <>
                            <Send className="h-3 w-3 shrink-0" />
                            {bot.chat_id || '—'}
                          </>
                        ) : (
                          <>
                            <Hash className="h-3 w-3 shrink-0" />
                            {bot.channel || (bot.kind === 'webhook' ? 'webhook' : '—')}
                          </>
                        )}
                      </div>
                    </div>
                    <span className="flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-emerald-300">
                      <CheckCircle2 className="h-3 w-3" />
                      מחובר
                    </span>
                  </div>
                  <div className="flex items-center gap-2 border-t border-white/[0.06] pt-3">
                    <button
                      onClick={() => onTest(bot.id)}
                      className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-white/[0.04] py-2 text-xs font-medium text-slate-200 transition-colors hover:bg-white/[0.08]"
                    >
                      <BellRing className="h-3.5 w-3.5" />
                      בדיקה
                    </button>
                    <button
                      onClick={() => onRemove(bot.id)}
                      className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-rose-400/10 hover:text-rose-400"
                      aria-label="מחק בוט"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button
            onClick={onAdd}
            className={`mt-4 flex w-full items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition-colors ${cfg.addBtn}`}
          >
            <Plus className="h-4 w-4" />
            {bots.length === 0 ? 'הוסף בוט ראשון' : 'הוסף בוט'}
          </button>
        </div>
      </div>
    </div>
  );
}
