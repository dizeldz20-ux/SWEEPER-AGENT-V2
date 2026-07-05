import {useEffect, useRef, useState} from 'react';
import {BotMessageSquare, Send, Sparkles} from 'lucide-react';

type Role = 'user' | 'assistant' | 'system';

interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  at: number;
}

const SUGGESTIONS = [
  'מה מצב המכונות כרגע?',
  'סכם את האירועים האחרונים',
  'אילו התראות פתוחות דורשות טיפול?',
  'תן חיזוי עומס לשעות הקרובות',
];

function wsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws`;
}

function uid() {
  const c = window.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `web-${Math.random().toString(36).slice(2)}`;
}

function fmtTime(at: number) {
  try {
    return new Date(at).toLocaleTimeString('he-IL', {hour: '2-digit', minute: '2-digit'});
  } catch {
    return '';
  }
}

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [status, setStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const socket = useRef<WebSocket | null>(null);
  const sessionId = useRef<string>(uid());
  const inputRef = useRef<HTMLInputElement | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      setStatus('closed');
      return;
    }
    socket.current = ws;
    ws.onopen = () => setStatus('open');
    ws.onclose = () => setStatus('closed');
    ws.onerror = () => setStatus('closed');
    ws.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.assistant?.content) {
          setMessages((prev) => [
            ...prev,
            {id: uid(), role: 'assistant', content: payload.assistant.content, at: Date.now()},
          ]);
        }
        // Backend housekeeping errors (e.g. "not_found" when no agent is live)
        // are intentionally swallowed — the panel should never look broken while
        // the Hermes agent is the one that drives replies in the background.
      } catch {
        const text = String(event.data ?? '').trim();
        if (text) {
          setMessages((prev) => [...prev, {id: uid(), role: 'assistant', content: text, at: Date.now()}]);
        }
      }
    };
    return () => ws.close();
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({behavior: 'smooth', block: 'end'});
  }, [messages]);

  const send = (text?: string) => {
    const content = (text ?? draft).trim();
    if (!content) return;
    // Always echo the user's message locally so the chat stays responsive even
    // when no agent is connected yet — the reply arrives once Hermes is live.
    setMessages((prev) => [...prev, {id: uid(), role: 'user', content, at: Date.now()}]);
    if (socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({session_id: sessionId.current, content}));
    }
    setDraft('');
  };

  const useSuggestion = (s: string) => {
    setDraft(s);
    inputRef.current?.focus();
  };

  const hasConversation = messages.some((m) => m.role !== 'system');

  const statusMeta =
    status === 'open'
      ? {label: 'מחובר', dot: 'bg-emerald-400', text: 'text-emerald-300', ring: 'border-emerald-500/20 bg-emerald-500/10'}
      : status === 'connecting'
        ? {label: 'מתחבר…', dot: 'bg-amber-400', text: 'text-amber-200', ring: 'border-amber-500/20 bg-amber-500/10'}
        : {label: 'במצב המתנה', dot: 'bg-slate-500', text: 'text-slate-300', ring: 'border-slate-600/40 bg-slate-700/20'};

  return (
    <div className="p-8 flex flex-col gap-6 view-in" dir="rtl">
      <header className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="relative w-11 h-11 rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-lg shadow-indigo-600/30">
            <BotMessageSquare className="w-6 h-6 text-white" />
            <span className={`absolute -bottom-0.5 -left-0.5 w-3 h-3 rounded-full border-2 border-slate-950 ${statusMeta.dot}`} />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-white tracking-tight" style={{fontFamily: 'var(--font-display)'}}>
              עוזר AI
            </h2>
            <p className="text-sm text-slate-400">העוזר החכם של Sweeper · פועל דרך סוכן הרמס ברקע</p>
          </div>
        </div>
        <span className={`inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border ${statusMeta.ring} ${statusMeta.text}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${statusMeta.dot}`} />
          {statusMeta.label}
        </span>
      </header>

      <div className="chat-surface relative rounded-3xl border border-slate-800 flex flex-col h-[70vh] min-h-[460px] overflow-hidden shadow-2xl shadow-black/40">
        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-6">
          {hasConversation ? (
            <div className="flex flex-col gap-4">
              {messages.map((m) => {
                if (m.role === 'system') {
                  return (
                    <div key={m.id} className="self-center chat-in">
                      <div className="rounded-full border border-slate-700/40 bg-slate-800/40 px-3 py-1 text-[11px] text-slate-400">
                        {m.content}
                      </div>
                    </div>
                  );
                }
                if (m.role === 'user') {
                  return (
                    <div key={m.id} className="self-start max-w-[78%] chat-in">
                      <div className="rounded-2xl rounded-ss-md bg-gradient-to-br from-indigo-500 to-violet-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-lg shadow-indigo-900/30 text-start">
                        <p className="whitespace-pre-wrap break-words">{m.content}</p>
                        <div className="mt-1 text-[10px] text-indigo-100/70 text-start" dir="ltr">
                          {fmtTime(m.at)}
                        </div>
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={m.id} className="self-end max-w-[82%] flex items-end gap-2.5 chat-in">
                    <div className="rounded-2xl rounded-se-md border border-slate-700/70 bg-slate-800/70 px-4 py-2.5 text-sm leading-relaxed text-slate-100 shadow-md shadow-black/20 text-start backdrop-blur-sm">
                      <p className="whitespace-pre-wrap break-words">{m.content}</p>
                      <div className="mt-1 text-[10px] text-slate-500 text-start" dir="ltr">
                        {fmtTime(m.at)}
                      </div>
                    </div>
                    <div className="shrink-0 w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-500/25 to-violet-600/25 border border-indigo-500/30 flex items-center justify-center">
                      <BotMessageSquare className="w-4 h-4 text-indigo-300" />
                    </div>
                  </div>
                );
              })}
              <div ref={endRef} />
            </div>
          ) : (
            <div className="h-full flex flex-col items-center justify-center text-center gap-5 px-6">
              <div className="relative w-16 h-16 rounded-3xl bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center shadow-xl shadow-indigo-600/30">
                <Sparkles className="w-8 h-8 text-white" />
                <span className="absolute inset-0 rounded-3xl ring-1 ring-white/10" />
              </div>
              <div className="max-w-md space-y-2">
                <h3 className="text-xl font-bold text-white" style={{fontFamily: 'var(--font-display)'}}>
                  היי, אני העוזר של Sweeper
                </h3>
                <p className="text-sm leading-relaxed text-slate-400">
                  שאל אותי על מצב המכונות, התראות פתוחות או חיזויי עומס. הסוכן החכם (הרמס) פועל ברקע —
                  ההודעות יופיעו כאן וייענו בזמן אמת כשהוא פעיל.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => useSuggestion(s)}
                    className="rounded-full border border-slate-700/70 bg-slate-800/40 px-4 py-2 text-xs text-slate-300 transition hover:border-indigo-500/50 hover:bg-slate-800 hover:text-white"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-slate-800/80 bg-slate-950/40 px-4 py-4 backdrop-blur-sm">
          <div className="flex items-center gap-2 rounded-2xl border border-slate-800 bg-slate-950/70 p-2 transition focus-within:border-indigo-500/60 focus-within:ring-4 focus-within:ring-indigo-500/10">
            <button
              onClick={() => send()}
              disabled={!draft.trim()}
              aria-label="שליחה"
              className="shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white flex items-center justify-center shadow-lg shadow-indigo-600/25 transition hover:from-indigo-400 hover:to-violet-500 disabled:opacity-40 disabled:shadow-none disabled:cursor-not-allowed"
            >
              <Send className="w-4 h-4" style={{transform: 'scaleX(-1)'}} />
            </button>
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send();
              }}
              className="flex-1 bg-transparent px-2 text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none"
              placeholder="שאל את הסוכן…"
            />
          </div>
          <p className="mt-2 px-1 text-[11px] text-slate-500">
            {status === 'open'
              ? 'מחובר · לחצו Enter לשליחה'
              : 'הסוכן במצב המתנה · ההודעות יופיעו כאן וייענו כשהרמס יפעיל את הסוכן'}
          </p>
        </div>
      </div>
    </div>
  );
}
