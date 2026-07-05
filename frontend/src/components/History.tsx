import React, { useState } from 'react';
import { Alert } from '../types';
import { Search, Filter, AlertTriangle, Info, AlertOctagon, CheckSquare, Square, Calendar, Download, Printer } from 'lucide-react';

interface HistoryProps {
  alerts: Alert[];
  onToggleAcknowledge?: (id: string) => void;
  onUpdateAlertStatus?: (id: string, status: Alert['status']) => void;
  onSnoozeAlert?: (id: string, durationMinutes: number) => void;
}

export function History({ alerts, onToggleAcknowledge, onSnoozeAlert, onUpdateAlertStatus }: HistoryProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [filterLevel, setFilterLevel] = useState<string>('all');
  const [filterType, setFilterType] = useState<string>('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  const filteredAlerts = alerts.filter(alert => {
    const matchesSearch = alert.message.toLowerCase().includes(searchTerm.toLowerCase()) || 
                          alert.machineName.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesLevel = filterLevel === 'all' || alert.level === filterLevel;
    const matchesType = filterType === 'all' || alert.eventType === filterType;
    
    let matchesDate = true;
    if (startDate) {
      matchesDate = matchesDate && new Date(alert.timestamp) >= new Date(startDate);
    }
    if (endDate) {
      // Add 1 day to end date to make it inclusive of the selected day
      const end = new Date(endDate);
      end.setDate(end.getDate() + 1);
      matchesDate = matchesDate && new Date(alert.timestamp) < end;
    }

    return matchesSearch && matchesLevel && matchesType && matchesDate;
  });

  const getLevelIcon = (level: string) => {
    switch (level) {
      case 'critical': return <AlertOctagon className="w-4 h-4 text-rose-400" />;
      case 'warning': return <AlertTriangle className="w-4 h-4 text-amber-400" />;
      default: return <Info className="w-4 h-4 text-emerald-400" />;
    }
  };

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'critical': return 'text-rose-400 bg-rose-400/10 border-rose-400/20';
      case 'warning': return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
      default: return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
    }
  };

  const exportToCSV = () => {
    const headers = ['ID', 'Machine Name', 'Level', 'Type', 'Status', 'Message', 'Timestamp'];
    
    const csvContent = [
      headers.join(','),
      ...filteredAlerts.map(alert => [
        alert.id,
        `"${alert.machineName}"`,
        alert.level,
        alert.eventType,
        alert.status,
        `"${alert.message.replace(/"/g, '""')}"`,
        `"${new Date(alert.timestamp).toLocaleString()}"`
      ].join(','))
    ].join('\n');

    const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `events_export_${new Date().toISOString().split('T')[0]}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="p-6 lg:p-8 flex flex-col gap-6 view-in" dir="rtl">

      <header className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-[26px] font-extrabold tracking-tight text-white">יומן אירועים</h2>
            <p className="text-sm text-slate-400 mt-1"><span className="num">{filteredAlerts.length}</span> אירועים תואמים לסינון</p>
          </div>
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => window.print()}
              className="flex items-center gap-2 px-4 py-2.5 bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 border border-white/[0.06] rounded-xl text-sm font-semibold transition-colors"
            >
              <Printer className="w-4 h-4" />
              דו"ח PDF
            </button>
            <button
              onClick={exportToCSV}
              className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-b from-indigo-500 to-indigo-600 hover:from-indigo-400 hover:to-indigo-500 text-white rounded-xl text-sm font-semibold transition-colors shadow-[0_8px_20px_-8px_rgba(99,102,241,0.8)]"
            >
              <Download className="w-4 h-4" />
              ייצוא ל-CSV
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-3">
          <div className="relative lg:col-span-2">
            <Search className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input 
              type="text" 
              placeholder="חיפוש אירוע או מכונה..." 
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl py-2 pr-10 pl-4 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-indigo-400/70 transition-colors focus:ring-1 focus:ring-indigo-500"
            />
          </div>
          
          <div className="relative">
            <Filter className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
            <select 
              value={filterLevel}
              onChange={(e) => setFilterLevel(e.target.value)}
              className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl py-2 pr-10 pl-8 text-sm text-slate-200 focus:outline-none focus:border-indigo-400/70 transition-colors appearance-none"
            >
              <option value="all">כל הרמות</option>
              <option value="critical">קריטי</option>
              <option value="warning">אזהרה</option>
              <option value="info">מידע</option>
            </select>
          </div>

          <div className="relative">
            <Filter className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
            <select 
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl py-2 pr-10 pl-8 text-sm text-slate-200 focus:outline-none focus:border-indigo-400/70 transition-colors appearance-none"
            >
              <option value="all">כל הסוגים</option>
              <option value="security">אבטחה</option>
              <option value="system">מערכת</option>
              <option value="performance">ביצועים</option>
              <option value="network">רשת</option>
            </select>
          </div>

          <div className="flex items-center gap-2 relative">
            <Calendar className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
            <input 
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl py-2 pr-10 pl-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-400/70 transition-colors"
              style={{ colorScheme: 'dark' }}
            />
            <span className="text-slate-500">-</span>
            <input 
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="w-full bg-white/[0.03] border border-white/[0.07] rounded-xl py-2 px-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-400/70 transition-colors"
              style={{ colorScheme: 'dark' }}
            />
          </div>
        </div>
      </header>

      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-right border-collapse">
            <thead>
              <tr className="border-b border-white/[0.06] bg-white/[0.02] text-slate-400 text-xs uppercase tracking-wide">
                <th className="py-4 px-6 font-semibold w-16 text-center">טופל</th>
                <th className="py-4 px-6 font-semibold">זמן אירוע</th>
                <th className="py-4 px-6 font-semibold">סוג</th>
                <th className="py-4 px-6 font-semibold">רמה</th>
                <th className="py-4 px-6 font-semibold">עדיפות</th>
                <th className="py-4 px-6 font-semibold">מכונה</th>
                <th className="py-4 px-6 font-semibold">תיאור אירוע</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {filteredAlerts.length > 0 ? (
                filteredAlerts.map(alert => (
                  <tr key={alert.id} className="hover:bg-white/[0.03] transition-colors">
                    <td className="py-4 px-6 text-center">
                      <button 
                        onClick={() => onToggleAcknowledge?.(alert.id)}
                        className={`transition-colors ${alert.acknowledged ? 'text-indigo-500 hover:text-indigo-400' : 'text-slate-600 hover:text-slate-400'}`}
                        title={alert.acknowledged ? 'סמן כלא טופל' : 'סמן כטופל'}
                      >
                        {alert.acknowledged ? <CheckSquare className="w-5 h-5 mx-auto" /> : <Square className="w-5 h-5 mx-auto" />}
                      </button>
                    </td>
                    <td className="py-4 px-6 text-sm text-slate-400 font-mono num" dir="ltr">{new Date(alert.timestamp).toLocaleString()}</td>
                    <td className="py-4 px-6 text-sm text-slate-300">
                      <span className="capitalize">{alert.eventType}</span>
                    </td>
                    <td className="py-4 px-6">
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border ${getLevelColor(alert.level)}`}>
                        {getLevelIcon(alert.level)}
                        {alert.level.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-4 px-6">
                      <span className={`inline-flex items-center justify-center px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider ${
                        alert.priority === 'urgent' ? 'bg-red-500/20 text-red-400 border border-red-500/30' :
                        alert.priority === 'high' ? 'bg-orange-500/20 text-orange-400 border border-orange-500/30' :
                        alert.priority === 'medium' ? 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30' :
                        'bg-slate-500/20 text-slate-400 border border-slate-500/30'
                      }`}>
                        {alert.priority}
                      </span>
                    </td>
                    <td className="py-4 px-6 text-sm font-medium text-slate-300">{alert.machineName}</td>
                    <td className="py-4 px-6 text-sm text-slate-300 max-w-md truncate" title={alert.message}>
                      <span className={alert.acknowledged ? 'opacity-50 line-through' : ''}>
                        {alert.message}
                      </span>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-500">
                    לא נמצאו אירועים התואמים לחיפוש
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
