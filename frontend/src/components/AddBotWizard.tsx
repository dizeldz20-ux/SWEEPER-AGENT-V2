import {useEffect, useState} from 'react';
import {
  Bot,
  CheckCircle2,
  ChevronLeft,
  Hash,
  KeyRound,
  Loader2,
  MessageSquare,
  Send,
  Tag,
  XCircle,
} from 'lucide-react';
import {Modal} from './Modal';
import {addBot, testBot} from '../services/endpoints';

// A focused "add one notification bot" dialog, mirroring AddMachineWizard's
// form → saving → result flow but for a single platform. Telegram collects a
// bot token + chat id; Slack collects a Slack App bot token (xoxb-…) + channel.
// On save it persists the bot, then fires a live test so the operator gets
// immediate proof the credentials work.

type Platform = 'telegram' | 'slack';
type Phase = 'form' | 'saving' | 'result';

interface AddBotWizardProps {
  open: boolean;
  platform: Platform;
  onClose: () => void;
  onSaved: () => void; // refresh the bot list in the parent
}

interface SaveResult {
  saved: boolean;
  tested: boolean;
  message?: string;
}

const META: Record<Platform, {label: string; kicker: string; accent: string; icon: typeof Bot}> = {
  telegram: {label: 'בוט Telegram', kicker: 'הוספת בוט Telegram', accent: '#0088cc', icon: Bot},
  slack: {label: 'אפליקציית Slack', kicker: 'הוספת Slack App', accent: '#611f69', icon: MessageSquare},
};

export function AddBotWizard({open, platform, onClose, onSaved}: AddBotWizardProps) {
  const [phase, setPhase] = useState<Phase>('form');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [chatId, setChatId] = useState('');
  const [channel, setChannel] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<SaveResult | null>(null);

  // Reset everything each time the wizard is (re)opened.
  useEffect(() => {
    if (!open) return;
    setPhase('form');
    setName('');
    setToken('');
    setChatId('');
    setChannel('');
    setError('');
    setResult(null);
  }, [open, platform]);

  const meta = META[platform];
  const Icon = meta.icon;

  const save = async () => {
    if (!token.trim()) {
      setError('יש למלא את הטוקן.');
      return;
    }
    if (platform === 'telegram' && !chatId.trim()) {
      setError('יש למלא מזהה צ׳אט (chat_id).');
      return;
    }
    if (platform === 'slack' && !channel.trim()) {
      setError('יש למלא ערוץ (channel).');
      return;
    }
    setError('');
    setPhase('saving');
    try {
      const res = await addBot({
        platform,
        name: name.trim() || undefined,
        bot_token: token.trim(),
        chat_id: platform === 'telegram' ? chatId.trim() : undefined,
        channel: platform === 'slack' ? channel.trim() : undefined,
      });
      if (!res.ok || !res.bot) {
        setError(res.error || 'שמירת הבוט נכשלה.');
        setPhase('form');
        return;
      }
      // Persisted — refresh the parent list now so it shows even if the live
      // test is slow or fails.
      onSaved();
      let tested = false;
      let message: string | undefined;
      try {
        const t = await testBot(res.bot.id);
        tested = !!t.ok;
        message = t.message || t.error;
      } catch (err) {
        message = err instanceof Error ? err.message : String(err);
      }
      setResult({saved: true, tested, message});
      setPhase('result');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase('form');
    }
  };

  const ghostBtn = 'flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-white/5 hover:text-slate-200';
  const primaryBtn = 'flex items-center gap-2 rounded-xl bg-indigo-500 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 ring-1 ring-inset ring-white/15 transition-colors hover:bg-indigo-400';

  const footer = phase === 'form' ? (
    <div className="flex items-center justify-between">
      <button type="button" onClick={onClose} className={ghostBtn}>ביטול</button>
      <button type="button" onClick={() => void save()} className={primaryBtn}>
        שמור והתחבר
        <ChevronLeft className="h-4 w-4" />
      </button>
    </div>
  ) : phase === 'result' ? (
    <div className="flex items-center justify-end">
      <button type="button" onClick={onClose} className={primaryBtn}>סיום</button>
    </div>
  ) : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      dismissable={phase !== 'saving'}
      widthClass="max-w-xl"
      kicker={meta.kicker}
      title={meta.label}
      footer={footer}
    >
      {phase === 'form' && (
        <BotForm
          platform={platform}
          accent={meta.accent}
          name={name}
          setName={setName}
          token={token}
          setToken={setToken}
          chatId={chatId}
          setChatId={setChatId}
          channel={channel}
          setChannel={setChannel}
          error={error}
        />
      )}
      {phase === 'saving' && <SavingState label={meta.label} />}
      {phase === 'result' && result && <ResultState label={meta.label} result={result} accent={meta.accent} Icon={Icon} />}
    </Modal>
  );
}

function BotForm({
  platform,
  accent,
  name,
  setName,
  token,
  setToken,
  chatId,
  setChatId,
  channel,
  setChannel,
  error,
}: {
  platform: Platform;
  accent: string;
  name: string;
  setName: (v: string) => void;
  token: string;
  setToken: (v: string) => void;
  chatId: string;
  setChatId: (v: string) => void;
  channel: string;
  setChannel: (v: string) => void;
  error: string;
}) {
  const field = 'w-full rounded-xl border border-white/10 bg-slate-800/40 px-3.5 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 transition-colors focus:border-indigo-400/70 focus:bg-slate-800/70 focus:outline-none focus:ring-2 focus:ring-indigo-500/20';
  const label = 'mb-1.5 flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.14em] text-slate-500';

  const hint =
    platform === 'telegram'
      ? 'צור בוט אצל @BotFather וקבל טוקן. את מזהה הצ׳אט (chat_id) אפשר לקבל דרך @userinfobot או getUpdates.'
      : 'צור Slack App, הוסף לו הרשאת chat:write, התקן אותו ל-Workspace והעתק את ה-Bot User OAuth Token (xoxb-…). הוסף את הבוט לערוץ היעד.';

  return (
    <div className="wizard-rise-in space-y-6">
      <p className="flex items-start gap-2.5 text-sm leading-relaxed text-slate-400">
        <span
          className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded"
          style={{backgroundColor: `${accent}22`, color: accent}}
        >
          <KeyRound className="h-3.5 w-3.5" />
        </span>
        <span>{hint}</span>
      </p>

      <div className="space-y-4">
        <div>
          <label className={label}><Tag className="h-3 w-3" />שם תצוגה <span className="normal-case text-slate-600">(אופציונלי)</span></label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={platform === 'telegram' ? 'בוט התראות ראשי' : 'Slack התראות'}
            className={field}
            autoFocus
          />
        </div>
        <div>
          <label className={label}><KeyRound className="h-3 w-3" />{platform === 'telegram' ? 'טוקן הבוט' : 'Bot User OAuth Token'}</label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder={platform === 'telegram' ? '123456:ABC-DEF…' : 'xoxb-…'}
            className={field}
            dir="ltr"
          />
        </div>
        {platform === 'telegram' ? (
          <div>
            <label className={label}><Send className="h-3 w-3" />מזהה צ׳אט · chat_id</label>
            <input
              value={chatId}
              onChange={(e) => setChatId(e.target.value)}
              placeholder="-100123456789"
              className={field}
              dir="ltr"
            />
          </div>
        ) : (
          <div>
            <label className={label}><Hash className="h-3 w-3" />ערוץ · channel</label>
            <input
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
              placeholder="#alerts"
              className={field}
              dir="ltr"
            />
          </div>
        )}
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

function SavingState({label}: {label: string}) {
  return (
    <div className="wizard-rise-in flex flex-col items-center justify-center gap-5 py-14 text-center">
      <div className="relative flex h-16 w-16 items-center justify-center">
        <span className="absolute inset-0 rounded-full border border-indigo-500/20" />
        <span className="absolute inset-0 animate-ping rounded-full bg-indigo-500/10" />
        <Loader2 className="h-8 w-8 animate-spin text-indigo-400" />
      </div>
      <div className="space-y-1.5">
        <div className="font-display text-base font-semibold text-white">שומר ובודק חיבור</div>
        <div className="text-sm text-slate-400">שומר את {label} ושולח הודעת בדיקה…</div>
      </div>
    </div>
  );
}

function ResultState({
  label,
  result,
  accent,
  Icon,
}: {
  label: string;
  result: SaveResult;
  accent: string;
  Icon: typeof Bot;
}) {
  const ok = result.tested;
  return (
    <div className="wizard-rise-in flex flex-col items-center justify-center gap-5 py-10 text-center">
      <div
        className={`flex h-16 w-16 items-center justify-center rounded-full ring-1 ${
          ok ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/30' : 'bg-amber-500/10 text-amber-400 ring-amber-500/30'
        }`}
        style={ok ? undefined : {color: accent}}
      >
        {ok ? <CheckCircle2 className="h-8 w-8" /> : <Icon className="h-8 w-8" />}
      </div>
      <div className="space-y-1.5">
        <div className="font-display text-xl font-semibold text-white">
          {ok ? `${label} מחובר` : `${label} נשמר`}
        </div>
        <div className="mx-auto max-w-md text-sm leading-relaxed text-slate-400">
          {ok
            ? 'הבוט נשמר והודעת הבדיקה נשלחה בהצלחה. הוא יקבל מעכשיו את כל ההתראות.'
            : 'הבוט נשמר, אך בדיקת השליחה לא הצליחה. בדוק את הטוקן והיעד ונסה שוב מכרטיס הבוט.'}
        </div>
      </div>
      {result.message ? (
        <div
          className={`mx-auto max-w-md rounded-xl border px-4 py-2.5 text-xs ${
            ok ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300' : 'border-amber-500/20 bg-amber-500/10 text-amber-300'
          }`}
          dir="ltr"
        >
          {result.message}
        </div>
      ) : null}
    </div>
  );
}
