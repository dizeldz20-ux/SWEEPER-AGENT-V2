import {useMemo} from 'react';
import {Activity, TrendingUp} from 'lucide-react';
import {Line, LineChart, ResponsiveContainer, YAxis} from 'recharts';
import type {Machine} from '../types';

interface CpuSparklineWidgetProps {
  machines: Machine[];
}

export function CpuSparklineWidget({machines}: CpuSparklineWidgetProps) {
  const cpu = machines.length
    ? Math.round(machines.reduce((sum, m) => sum + (m.cpuUsage || 0), 0) / machines.length)
    : 0;
  const data = useMemo(
    () => Array.from({length: 10}).map((_, i) => ({value: Math.max(0, Math.min(100, cpu + i - 5))})),
    [cpu],
  );

  return (
    <div className="panel panel-hover p-5 flex flex-col justify-between">
      <div className="flex justify-between items-start mb-2">
        <div>
          <h3 className="text-[13px] font-medium text-slate-400">עומס מעבד ממוצע</h3>
          <div className="flex items-center gap-2 mt-2.5">
            <span className="text-[34px] leading-none font-extrabold text-white num">{cpu}%</span>
            <span className={`flex items-center text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
              cpu >= 85
                ? 'text-rose-300 bg-rose-500/10 border-rose-500/25'
                : 'text-emerald-300 bg-emerald-500/10 border-emerald-500/25'
            }`}>
              <TrendingUp className="w-3 h-3 ml-1" />
              {cpu >= 85 ? 'גבוה' : 'יציב'}
            </span>
          </div>
        </div>
        <div className="icon-tile w-12 h-12 bg-cyan-500/12 text-cyan-300">
          <Activity className="w-5 h-5" />
        </div>
      </div>

      <div className="h-12 w-full mt-2">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <YAxis domain={[0, 100]} hide />
            <Line
              type="monotone"
              dataKey="value"
              stroke={cpu >= 85 ? '#f43f5e' : '#10b981'}
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4 pt-3.5 border-t border-white/[0.05] flex items-center justify-between text-xs">
        <span className="text-slate-500">מקור:</span>
        <span className="text-slate-300 bg-white/[0.04] border border-white/[0.06] px-2 py-1 rounded-md font-mono num" dir="ltr">/api/fleet + /api/snapshot</span>
      </div>
    </div>
  );
}
