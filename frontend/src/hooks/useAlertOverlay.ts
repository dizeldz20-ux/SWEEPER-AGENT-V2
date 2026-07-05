import {useCallback, useEffect, useRef, useState} from 'react';
import type {Alert} from '../types';

// The /v6/alerts feed has no server-side per-alert state (resolved / in-progress
// / acknowledged / snoozed). We keep that workflow state client-side, keyed by
// the synthesized alert id, persisted in localStorage so it survives reloads.
// Machine maintenance is likewise tracked optimistically here (the fleet API
// does not yet report it).

export interface AlertOverlayEntry {
  status?: Alert['status'];
  acknowledged?: boolean;
  snoozedUntil?: string;
}

export interface MaintenanceEntry {
  maintenanceMode: boolean;
  maintenanceEndTime?: string;
}

const ALERT_KEY = 'sweeper.alertOverlay.v1';
const MAINT_KEY = 'sweeper.maintOverlay.v1';

function loadMap<T>(key: string): Record<string, T> {
  try {
    return JSON.parse(localStorage.getItem(key) || '{}') as Record<string, T>;
  } catch {
    return {};
  }
}

export function useOverlay() {
  const [alertOverlay, setAlertOverlay] = useState<Record<string, AlertOverlayEntry>>(
    () => loadMap(ALERT_KEY),
  );
  const [maintOverlay, setMaintOverlay] = useState<Record<string, MaintenanceEntry>>(
    () => loadMap(MAINT_KEY),
  );

  const maintRef = useRef(maintOverlay);
  maintRef.current = maintOverlay;

  useEffect(() => {
    try {
      localStorage.setItem(ALERT_KEY, JSON.stringify(alertOverlay));
    } catch {
      /* storage may be unavailable — non-fatal */
    }
  }, [alertOverlay]);

  useEffect(() => {
    try {
      localStorage.setItem(MAINT_KEY, JSON.stringify(maintOverlay));
    } catch {
      /* non-fatal */
    }
  }, [maintOverlay]);

  const patchAlert = useCallback((id: string, patch: AlertOverlayEntry) => {
    setAlertOverlay((prev) => ({...prev, [id]: {...prev[id], ...patch}}));
  }, []);

  const toggleAck = useCallback((id: string) => {
    setAlertOverlay((prev) => {
      const cur = prev[id]?.acknowledged ?? false;
      return {...prev, [id]: {...prev[id], acknowledged: !cur}};
    });
  }, []);

  const setMaint = useCallback((id: string, entry: MaintenanceEntry | null) => {
    setMaintOverlay((prev) => {
      const next = {...prev};
      if (entry === null) delete next[id];
      else next[id] = entry;
      return next;
    });
  }, []);

  const isMaintOn = useCallback((id: string) => !!maintRef.current[id]?.maintenanceMode, []);

  const applyAlerts = useCallback(
    (alerts: Alert[]): Alert[] =>
      alerts.map((a) => (alertOverlay[a.id] ? {...a, ...alertOverlay[a.id]} : a)),
    [alertOverlay],
  );

  const applyMaint = useCallback(
    <M extends {id: string; maintenanceMode?: boolean; maintenanceEndTime?: string}>(
      machines: M[],
    ): M[] =>
      machines.map((m) =>
        maintOverlay[m.id]
          ? {
              ...m,
              maintenanceMode: maintOverlay[m.id].maintenanceMode,
              maintenanceEndTime: maintOverlay[m.id].maintenanceEndTime,
            }
          : m,
      ),
    [maintOverlay],
  );

  return {patchAlert, toggleAck, setMaint, isMaintOn, applyAlerts, applyMaint};
}
