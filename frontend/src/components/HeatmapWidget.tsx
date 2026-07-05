import {useMemo} from 'react';
import {Activity} from 'lucide-react';
import {getHeatmap} from '../services/endpoints';
import {usePolling} from '../hooks/usePolling';

const getHeatmapColor = (value: number) => {
  if (value === 0) return 'bg-white/[0.04]';
  if (value < 10) return 'bg-indigo-500/25';
  if (value < 25) return 'bg-indigo-500/45';
  if (value < 40) return 'bg-indigo-500/70 shadow-[0_0_10px_-2px_rgba(99,102,241,0.7)]';
  return 'bg-indigo-400 shadow-[0_0_14px_-1px_rgba(99,102,241,0.9)]';
};

export function HeatmapWidget() {
  const {data} = usePolling((signal) => getHeatmap(signal), 30000);
  const buckets = useMemo(() => {
    const lastRow = data?.grid?.[data.grid.length - 1] || [];
    return Array.from({length: 24}).map((_, hour) => ({
      hour,
      value: Number(lastRow[hour] || 0),
    }));
  }, [data]);

  return (
    <div className="panel p-6 flex flex-col justify-between h-[350px]">
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-[17px] font-bold text-white">צפיפות אירועים ב-24 השעות</h3>
          <p className="text-xs text-slate-500 mt-1 font-mono" dir="ltr">/v6/metrics/events_heatmap</p>
        </div>
        <div className="icon-tile w-11 h-11 bg-indigo-500/12 text-indigo-300">
          <Activity className="w-5 h-5" />
        </div>
      </div>

      <div className="flex-1 flex flex-col justify-end mt-4">
        <div className="grid grid-cols-12 gap-1.5 h-24 md:[grid-template-columns:repeat(24,minmax(0,1fr))]" dir="ltr">
          {buckets.map((bucket) => (
            <div key={bucket.hour} className="flex flex-col items-center group relative h-full">
              <div
                className={`w-full h-full rounded-md ${getHeatmapColor(bucket.value)} transition-all cursor-pointer group-hover:ring-1 group-hover:ring-indigo-300 group-hover:scale-y-105 origin-bottom`}
              />
              <div className="absolute bottom-full mb-2 hidden group-hover:flex flex-col items-center z-10 w-max">
                <span className="relative z-10 p-2 text-xs leading-none text-white whitespace-no-wrap glass shadow-xl rounded-lg num" dir="ltr">
                  {`${bucket.hour.toString().padStart(2, '0')}:00 — ${bucket.value} events`}
                </span>
                <div className="w-3 h-3 -mt-2 rotate-45 bg-[rgba(15,20,33,0.72)] border-b border-r border-white/10" />
              </div>
              <span className="text-[10px] text-slate-500 mt-2 hidden md:block">
                {bucket.hour % 4 === 0 ? bucket.hour.toString().padStart(2, '0') : ''}
              </span>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-between mt-4 text-[10px] text-slate-500 border-t border-white/[0.05] pt-3">
          <span className="num" dir="ltr">00:00</span>
          <div className="flex items-center gap-1 mx-4" dir="ltr">
            <span className="mr-2 text-slate-400">נמוך</span>
            <div className="w-3 h-3 rounded-sm bg-white/[0.04]" />
            <div className="w-3 h-3 rounded-sm bg-indigo-500/25" />
            <div className="w-3 h-3 rounded-sm bg-indigo-500/45" />
            <div className="w-3 h-3 rounded-sm bg-indigo-500/70" />
            <div className="w-3 h-3 rounded-sm bg-indigo-400" />
            <span className="ml-2 text-slate-400">גבוה</span>
          </div>
          <span className="num" dir="ltr">23:00</span>
        </div>
      </div>
    </div>
  );
}
