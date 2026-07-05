import {
  Activity,
  BotMessageSquare,
  ClipboardCheck,
  History,
  LayoutDashboard,
  Settings,
  ShieldCheck,
} from 'lucide-react';
import type {ViewState} from '../types';

interface SidebarProps {
  currentView: ViewState;
  onChangeView: (view: ViewState) => void;
}

const navItems: Array<{id: ViewState; label: string; icon: typeof LayoutDashboard}> = [
  {id: 'dashboard', label: 'לוח בקרה', icon: LayoutDashboard},
  {id: 'machines', label: 'מכונות', icon: Activity},
  {id: 'history', label: 'היסטוריית אירועים', icon: History},
  {id: 'approvals', label: 'אישורים', icon: ClipboardCheck},
  {id: 'chat', label: 'עוזר AI', icon: BotMessageSquare},
  {id: 'settings', label: 'הגדרות', icon: Settings},
];

export function Sidebar({currentView, onChangeView}: SidebarProps) {
  return (
    <aside className="w-64 shrink-0 glass border-l border-slate-800/60 flex flex-col p-5 relative z-20">
      <div className="flex items-center gap-3 mb-9 px-1">
        <div className="relative w-10 h-10 rounded-2xl grid place-items-center bg-gradient-to-br from-indigo-500 to-indigo-700 shadow-[0_8px_24px_-8px_rgba(99,102,241,0.8)]">
          <div className="absolute inset-0 rounded-2xl ring-1 ring-inset ring-white/20" />
          <ShieldCheck className="w-5 h-5 text-white" strokeWidth={2.2} />
        </div>
        <div className="leading-tight">
          <h1 className="text-[15px] font-extrabold tracking-tight text-white">Sweeper Agent</h1>
          <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-slate-500">Linux Fleet</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1 flex-1" dir="rtl">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onChangeView(item.id)}
              className={`group relative flex items-center gap-3 px-3.5 py-2.5 rounded-xl transition-all duration-200 ${
                isActive ? 'text-white bg-indigo-500/10' : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]'
              }`}
            >
              <span
                className={`absolute right-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-full bg-gradient-to-b from-indigo-400 to-cyan-400 transition-all duration-300 ${
                  isActive ? 'opacity-100 shadow-[0_0_10px_rgba(99,102,241,0.9)]' : 'opacity-0'
                }`}
              />
              <Icon
                className={`w-[18px] h-[18px] shrink-0 transition-colors ${
                  isActive ? 'text-indigo-300' : 'text-slate-500 group-hover:text-slate-300'
                }`}
                strokeWidth={2}
              />
              <span className="text-[13px] font-semibold">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto pt-4" dir="rtl">
        <div className="p-3.5 rounded-2xl border border-slate-700/40 bg-white/[0.02]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-medium text-slate-400">סטטוס חיבור</span>
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.9)]" />
            </span>
          </div>
          <p className="text-[11px] text-slate-300 font-mono num" dir="ltr">Same-origin API</p>
        </div>
      </div>
    </aside>
  );
}
