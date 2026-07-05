import {useState} from 'react';
import type {ViewState} from './types';
import {Sidebar} from './components/Sidebar';
import {Dashboard} from './components/Dashboard';
import {History} from './components/History';
import {Settings} from './components/Settings';
import {MachineList} from './components/MachineList';
import {Approvals} from './components/Approvals';
import {Chat} from './components/Chat';
import {DataProvider, useData} from './context/DataContext';

function AppShell() {
  const [currentView, setCurrentView] = useState<ViewState>('dashboard');
  const {
    alerts,
    machines,
    error,
    toggleAcknowledge,
    updateAlertStatus,
    snooze,
  } = useData();

  return (
    <div className="flex h-screen overflow-hidden text-slate-200 font-sans" dir="rtl">
      <Sidebar currentView={currentView} onChangeView={setCurrentView} />

      <main className="flex-1 overflow-y-auto relative">
        {error ? (
          <div className="absolute end-6 top-6 z-30 glass rounded-xl border-rose-500/30 bg-rose-500/10 px-4 py-2 text-xs text-rose-200 shadow-lg shadow-rose-900/20 view-in" dir="rtl">
            תקלת חיבור ל-API · מוצגים הנתונים האחרונים שנשמרו
          </div>
        ) : null}
        <div className="relative z-10 min-h-full">
          {currentView === 'dashboard' && (
            <Dashboard
              machines={machines}
              alerts={alerts}
              onUpdateAlertStatus={updateAlertStatus}
              onSnoozeAlert={snooze}
            />
          )}
          {currentView === 'machines' && (
            <MachineList machines={machines} />
          )}
          {currentView === 'history' && (
            <History
              alerts={alerts}
              onToggleAcknowledge={toggleAcknowledge}
              onUpdateAlertStatus={updateAlertStatus}
              onSnoozeAlert={snooze}
            />
          )}
          {currentView === 'approvals' && <Approvals />}
          {currentView === 'chat' && <Chat />}
          {currentView === 'settings' && <Settings />}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <DataProvider>
      <AppShell />
    </DataProvider>
  );
}
