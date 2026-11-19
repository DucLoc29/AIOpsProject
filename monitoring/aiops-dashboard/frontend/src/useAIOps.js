import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';

/** Polling state + database snapshot từ Backend (2s/lần) và các hành động điều khiển + CRUD Services. */
export function useAIOps(intervalMs = 2000) {
  const [state, setState] = useState(null);
  const [db, setDb] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([api.getState(), api.getDatabase()]);
      setState(s);
      setDb(d);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [refresh, intervalMs]);

  const act = useCallback(
    async (fn) => {
      try {
        setState(await fn());
        setDb(await api.getDatabase());
      } catch (e) {
        setError(e.message);
      }
    },
    []
  );

  return {
    state,
    db,
    error,
    simulate: (scenario) => act(() => api.simulate(scenario)),
    setPolicy: (payload) => act(() => api.setPolicy(payload)),
    approve: (service_id) => act(() => api.approve(service_id)),
    rollback: (service_id) => act(() => api.rollback(service_id)),
    createService: (payload) => act(() => api.createService(payload)),
    updateService: (service_id, payload) => act(() => api.updateService(service_id, payload)),
    deleteService: (service_id) => act(() => api.deleteService(service_id)),
  };
}
