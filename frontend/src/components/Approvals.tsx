import {Check, RefreshCw, RotateCcw, Server, ShieldAlert, X} from 'lucide-react';
import {approveProposal, rejectProposal, unblockKey} from '../services/endpoints';
import {useApprovals} from '../hooks/useApprovals';
import {useBlocked} from '../hooks/useBlocked';

export function Approvals() {
  const {approvals, loading, refetch} = useApprovals();
  const {blocked, refetch: refetchBlocked} = useBlocked();

  const decide = (id: string, kind: 'approve' | 'reject') => {
    const op = kind === 'approve' ? approveProposal(id) : rejectProposal(id);
    void op.finally(() => {
      refetch();
      refetchBlocked();
    });
  };

  const allowRetry = (key: string) => {
    void unblockKey(key).finally(refetchBlocked);
  };

  return (
    <div className="p-6 lg:p-8 flex flex-col gap-6 view-in" dir="rtl">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-[26px] font-extrabold tracking-tight text-white">תור אישורים</h2>
          <p className="text-sm text-slate-400 mt-1">פעולות מסוכנות ממתינות כאן לאישור לפני ביצוע.</p>
        </div>
        <button
          onClick={() => {
            refetch();
            refetchBlocked();
          }}
          className="p-2.5 rounded-xl bg-white/[0.04] text-slate-300 border border-white/[0.06] hover:bg-white/[0.08] hover:text-white transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </header>

      <div className="panel overflow-hidden">
        {loading ? (
          <div className="p-8 text-slate-500">טוען אישורים...</div>
        ) : approvals.length === 0 ? (
          <div className="p-12 flex flex-col items-center gap-3 text-slate-500">
            <div className="icon-tile w-14 h-14 bg-emerald-500/10 text-emerald-400/70"><Check className="w-7 h-7" /></div>
            אין אישורים ממתינים.
          </div>
        ) : (
          <div className="divide-y divide-white/[0.05]">
            {approvals.map((approval) => (
              <div key={approval.id} className="p-5 flex flex-col gap-4 hover:bg-white/[0.015] transition-colors">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-bold text-white">{approval.action}</span>
                      <span className="chip bg-white/[0.03] text-slate-300">
                        <Server className="w-3 h-3" />
                        <span className="num" dir="ltr">{approval.server || 'unknown'}</span>
                      </span>
                      <span className="text-xs text-slate-500 font-mono" dir="ltr">{approval.id}</span>
                    </div>
                    <p className="text-sm text-slate-400 mt-2">{approval.reason}</p>
                  </div>
                  <span className="text-xs text-slate-500 num shrink-0" dir="ltr">{new Date(approval.createdAt).toLocaleString()}</span>
                </div>
                <pre className="bg-[#04060c] border border-white/[0.06] rounded-xl p-3.5 text-xs text-slate-300 overflow-x-auto shadow-[inset_0_2px_16px_-8px_rgba(0,0,0,0.9)]" dir="ltr">
                  {approval.proposedCommand || '# no command'}
                </pre>
                <div className="flex justify-end gap-2">
                  <button
                    onClick={() => decide(approval.id, 'reject')}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20 text-sm transition-colors"
                  >
                    <X className="w-4 h-4" />
                    דחה
                  </button>
                  <button
                    onClick={() => decide(approval.id, 'approve')}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20 text-sm transition-colors"
                  >
                    <Check className="w-4 h-4" />
                    אשר
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {blocked.length > 0 && (
        <div className="panel border-amber-500/30 overflow-hidden">
          <div className="p-5 flex items-center gap-3 border-b border-white/[0.06]">
            <div className="icon-tile w-10 h-10 bg-amber-500/15 text-amber-300 border-amber-500/25">
              <ShieldAlert className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-[17px] font-bold text-white">חסומים — נדרשת התערבות אנושית</h3>
              <p className="text-xs text-slate-400 mt-0.5">
                תיקונים שנכשלו לאחר אישור. הסוכן לא ינסה שוב עד שתאשרו ניסיון חוזר.
              </p>
            </div>
          </div>
          <div className="divide-y divide-white/[0.05]">
            {blocked.map((b) => (
              <div key={b.key} className="p-5 flex items-start justify-between gap-4 hover:bg-white/[0.015] transition-colors">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-bold text-white">{b.action}</span>
                    <span className="chip bg-white/[0.03] text-slate-300">
                      <Server className="w-3 h-3" />
                      <span className="num" dir="ltr">{b.server || 'unknown'}</span>
                    </span>
                  </div>
                  <p className="text-sm text-slate-400 mt-2">{b.reason}</p>
                  <span className="text-xs text-slate-500 num" dir="ltr">{new Date(b.blockedAt).toLocaleString()}</span>
                </div>
                <button
                  onClick={() => allowRetry(b.key)}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 text-sm shrink-0 transition-colors"
                >
                  <RotateCcw className="w-4 h-4" />
                  אפשר ניסיון חוזר
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
