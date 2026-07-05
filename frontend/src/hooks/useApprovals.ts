import {useMemo} from 'react';
import {approvalsToUi} from '../services/adapters';
import {listApprovals} from '../services/endpoints';
import {usePolling} from './usePolling';

export function useApprovals() {
  const state = usePolling(async (signal) => {
    const raw = await listApprovals(signal);
    return approvalsToUi(raw.pending || []);
  }, 15000);

  const approvals = useMemo(() => state.data || [], [state.data]);
  return {...state, approvals};
}
