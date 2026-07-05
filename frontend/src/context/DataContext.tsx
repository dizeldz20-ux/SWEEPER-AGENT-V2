import React, {createContext, useContext, useMemo} from 'react';
import type {Alert, Machine} from '../types';
import {clearMaintenance, machineAction, resolveAlert, setMaintenance, snoozeAlert} from '../services/endpoints';
import type {MachineAction} from '../services/endpoints';
import {useAlerts} from '../hooks/useAlerts';
import {useFleet} from '../hooks/useFleet';
import {useOverlay} from '../hooks/useAlertOverlay';

interface DataContextValue {
  machines: Machine[];
  alerts: Alert[];
  loading: boolean;
  error: unknown;
  refetch: () => void;
  toggleAcknowledge: (id: string) => void;
  updateAlertStatus: (id: string, status: Alert['status']) => void;
  snooze: (id: string, durationMinutes: number) => void;
  toggleMaintenance: (id: string, durationMinutes?: number) => void;
  requestMachineAction: (id: string, action: MachineAction) => void;
}

const DataContext = createContext<DataContextValue | null>(null);

export function DataProvider({children}: {children: React.ReactNode}) {
  const fleet = useFleet();
  const alertState = useAlerts();
  const overlay = useOverlay();

  const machines = useMemo(
    () => overlay.applyMaint(fleet.machines),
    [fleet.machines, overlay],
  );
  const alerts = useMemo(
    () => overlay.applyAlerts(alertState.alerts),
    [alertState.alerts, overlay],
  );

  const refetch = () => {
    fleet.refetch();
    alertState.refetch();
  };

  const updateAlertStatus = (id: string, status: Alert['status']) => {
    overlay.patchAlert(id, {status, acknowledged: status === 'resolved'});
    if (status === 'resolved') {
      void resolveAlert(id, 'resolved from React dashboard').finally(alertState.refetch);
    }
  };

  const snooze = (id: string, durationMinutes: number) => {
    overlay.patchAlert(id, {
      snoozedUntil: new Date(Date.now() + durationMinutes * 60000).toISOString(),
    });
    void snoozeAlert(id, durationMinutes).finally(alertState.refetch);
  };

  const toggleMaintenance = (id: string, durationMinutes?: number) => {
    if (overlay.isMaintOn(id)) {
      overlay.setMaint(id, null);
      void clearMaintenance(id).finally(fleet.refetch);
      return;
    }
    const duration = durationMinutes ?? 0;
    const end =
      duration > 0
        ? new Date(Date.now() + duration * 60000).toISOString()
        : undefined;
    overlay.setMaint(id, {maintenanceMode: true, maintenanceEndTime: end});
    void setMaintenance(id, duration).finally(fleet.refetch);
  };

  const requestMachineAction = (id: string, action: MachineAction) => {
    void machineAction(id, action).finally(fleet.refetch);
  };

  return (
    <DataContext.Provider
      value={{
        machines,
        alerts,
        loading: fleet.loading || alertState.loading,
        error: fleet.error || alertState.error,
        refetch,
        toggleAcknowledge: overlay.toggleAck,
        updateAlertStatus,
        snooze,
        toggleMaintenance,
        requestMachineAction,
      }}
    >
      {children}
    </DataContext.Provider>
  );
}

export function useData() {
  const value = useContext(DataContext);
  if (!value) throw new Error('useData must be used inside DataProvider');
  return value;
}
