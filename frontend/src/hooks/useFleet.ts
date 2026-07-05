import {useMemo} from 'react';
import {fleetToMachines} from '../services/adapters';
import {ApiError} from '../services/http';
import {getFleet, getSnapshot} from '../services/endpoints';
import {usePolling} from './usePolling';

export function useFleet() {
  const state = usePolling(async (signal) => {
    const [fleet, snapshot] = await Promise.all([
      getFleet(signal),
      getSnapshot(signal).catch((err) => {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }),
    ]);
    return fleetToMachines(fleet, snapshot);
  }, 10000);

  const machines = useMemo(() => state.data || [], [state.data]);
  return {...state, machines};
}
