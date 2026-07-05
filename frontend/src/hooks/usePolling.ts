import {useCallback, useEffect, useRef, useState} from 'react';

export interface PollingState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  refetch: () => void;
}

// Poll `fetcher` every `intervalMs`. Pauses while the tab is hidden, aborts the
// in-flight request on unmount, and exposes a manual refetch().
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    const ac = new AbortController();

    const run = async () => {
      try {
        const d = await fetcherRef.current(ac.signal);
        if (!ac.signal.aborted) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        const name = (e as {name?: string} | null)?.name;
        if (!ac.signal.aborted && name !== 'AbortError') setError(e);
      } finally {
        if (!ac.signal.aborted) setLoading(false);
      }
    };

    run();
    const id = window.setInterval(() => {
      if (!document.hidden) run();
    }, intervalMs);

    return () => {
      ac.abort();
      window.clearInterval(id);
    };
  }, [intervalMs, tick]);

  return {data, error, loading, refetch};
}
