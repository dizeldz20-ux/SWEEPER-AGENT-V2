import {useMemo} from 'react';
import {Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis} from 'recharts';
import {format} from 'date-fns';
import {getUptime30d} from '../services/endpoints';
import {usePolling} from '../hooks/usePolling';

export function UptimeChartWidget() {
  const {data} = usePolling((signal) => getUptime30d(signal), 60000);
  const points = useMemo(
    () =>
      (data?.points || []).map((p) => ({
        date: format(new Date(p.date), 'dd/MM'),
        uptime: Math.round((p.ratio || 0) * 10000) / 100,
      })),
    [data],
  );

  return (
    <div className="panel p-6 flex flex-col w-full h-[300px]">
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-[17px] font-bold text-white">אומדן זמן פעילות ב-30 הימים האחרונים</h3>
        <span className="chip bg-emerald-500/10 border-emerald-500/25 text-emerald-300"><span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />uptime</span>
      </div>
      <div className="flex-1 w-full h-full" dir="ltr">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{top: 10, right: 30, left: 0, bottom: 0}}>
            <defs>
              <linearGradient id="colorUptime" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="4 4" stroke="rgba(148,163,184,0.12)" vertical={false} />
            <XAxis dataKey="date" stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} dy={6} />
            <YAxis stroke="#64748b" fontSize={11} tickLine={false} axisLine={false} domain={[90, 100]} tickFormatter={(val) => `${val}%`} width={40} />
            <Tooltip
              cursor={{stroke: 'rgba(148,163,184,0.25)', strokeWidth: 1}}
              contentStyle={{backgroundColor: 'rgba(12,16,26,0.92)', backdropFilter: 'blur(8px)', border: '1px solid rgba(148,163,184,0.16)', borderRadius: '0.85rem', color: '#f8fafc', boxShadow: '0 20px 40px -20px rgba(0,0,0,0.9)'}}
              itemStyle={{color: '#10b981'}}
              formatter={(value: number) => [`${value.toFixed(2)}%`, 'זמן פעילות']}
            />
            <Area type="monotone" dataKey="uptime" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#colorUptime)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
