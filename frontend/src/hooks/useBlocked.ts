import {useMemo} from 'react';
import {blockedToUi} from '../services/adapters';
import {listBlocked} from '../services/endpoints';
import {usePolling} from './usePolling';

// Escalation blocks: (action, server) pairs frozen after an approved repair
// failed. Polls alongside the approvals queue.
export function useBlocked() {
  const state = usePolling(async (signal) => {
    const raw = await listBlocked(signal);
    return blockedToUi(raw.blocked || []);
  }, 15000);

  const blocked = useMemo(() => state.data || [], [state.data]);
  return {...state, blocked};
}
