import {useMemo} from 'react';
import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import type {Alert} from '../types';

interface MetricsChartProps {
  alerts: Alert[];
}

export function MetricsChart({alerts}: MetricsChartProps) {
  const data = useMemo(() => {
    const now = new Date();
    return Array.from({length: 7}).map((_, i) => {
      const start = new Date(now);
      start.setHours(now.getHours() - (6 - i) * 4, 0, 0, 0);
      const end = new Date(start);
      end.setHours(start.getHours() + 4);
      const bucketAlerts = alerts.filter((alert) => {
        const t = Date.parse(alert.timestamp);
        return !Number.isNaN(t) && t >= start.getTime() && t < end.getTime();
      });
      return {
        time: start.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}),
        events: bucketAlerts.length,
        alerts: bucketAlerts.filter((a) => a.level !== 'info').length,
      };
    });
  }, [alerts]);

  return (
    <div className="panel p-6 flex flex-col w-full h-[350px]">
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-[17px] font-bold text-white">מגמת אירועים ב-24 השעות האחרונות</h3>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="flex items-center gap-1.5 text-slate-400"><span className="w-2 h-2 rounded-full bg-indigo-400" />אירועים</span>
          <span className="flex items-center gap-1.5 text-slate-400"><span className="w-2 h-2 rounded-full bg-rose-400" />התראות</span>
        </div>
      </div>
      <div className="flex-1 w-full h-full" dir="ltr">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{top: 10, right: 30, left: 0, bottom: 0}}>
            <defs>
              <linearGradient id="colorEvents" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="colorAlerts" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#f43f5e" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="4 4" stroke="rgba(148,163,184,0.12)" vertical={false} />
            <XAxis dataKey="time" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} dy={6} />
            <YAxis stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} allowDecimals={false} width={28} />
            <Tooltip
              cursor={{stroke: 'rgba(148,163,184,0.25)', strokeWidth: 1}}
              contentStyle={{backgroundColor: 'rgba(12,16,26,0.92)', backdropFilter: 'blur(8px)', border: '1px solid rgba(148,163,184,0.16)', borderRadius: '0.85rem', color: '#f8fafc', boxShadow: '0 20px 40px -20px rgba(0,0,0,0.9)'}}
              itemStyle={{color: '#f8fafc'}}
              labelStyle={{color: '#94a3b8', fontSize: 11, marginBottom: 4}}
            />
            <Area type="monotone" dataKey="events" name="אירועים" stroke="#6366f1" strokeWidth={2} fillOpacity={1} fill="url(#colorEvents)" />
            <Area type="monotone" dataKey="alerts" name="התראות" stroke="#f43f5e" strokeWidth={2} fillOpacity={1} fill="url(#colorAlerts)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
