import {useMemo} from 'react';
import {alertsToUi} from '../services/adapters';
import {getAlerts} from '../services/endpoints';
import {usePolling} from './usePolling';

export function useAlerts() {
  const state = usePolling(async (signal) => {
    const raw = await getAlerts('all', signal);
    return alertsToUi(raw.alerts || []);
  }, 5000);

  const alerts = useMemo(() => state.data || [], [state.data]);
  return {...state, alerts};
}
