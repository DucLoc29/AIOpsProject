import { useCallback, useEffect, useState } from 'react';
import { api, tokenStore } from './api.js';

/** Polling state + database snapshot từ Backend (2s/lần) và các hành động điều khiển + CRUD Services. */
export function useAIOps(intervalMs = 2000) {
  const [state, setState] = useState(null);
  const [db, setDb] = useState(null);
  const [error, setError] = useState(null);

  const [user, setUser] = useState(null);
  // setting up auth
  const [authReady, setAuthReady] = useState(false);

  // Kiểm tra token sẵn có trong localStorage khi mới tải trang
  useEffect(() => {
    if (!tokenStore.get()) {
      setAuthReady(true);
      return;
    }
    api.me()
      .then((res) => setUser(res.user || null))
      .catch(() => {
        tokenStore.clear();
        setUser(null);
      })
      .finally(() => setAuthReady(true));
  }, []);


  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([api.getState(), api.getDatabase()]);
      setState(s);
      setDb(d);
      setError(null);
    } catch (e) {
      if (e.status === 401) {
        setUser(null);
        setState(null);
      } else {
        setError(e.message);
      }
    }
  }, []);

// Chỉ polling khi đã có user
  useEffect(() => {
    if (!user) return undefined;
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [user, refresh, intervalMs]);


  const login = useCallback(async (username, password) => {
    const res = await api.login(username, password);
    tokenStore.set(res.token);
    setUser(res.user);
    setError(null);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch (_) {
      // Bỏ qua lỗi mạng khi logout
    }
    tokenStore.clear();
    setUser(null);
    setState(null);
    setDb(null);
  }, []);

  const act = useCallback(
    async (fn) => {
      try {
        setState(await fn());
        setDb(await api.getDatabase());
      } catch (e) {
        if (e.status === 401) { 
          setUser(null); 
          return; 
        }
        setError(e.message);
      }
    },
    []
  );

  return {
    state,
    db,
    error,
    setPolicy: (payload) => act(() => api.setPolicy(payload)),
    approve: (service_id) => act(() => api.approve(service_id)),
    rollback: (service_id) => act(() => api.rollback(service_id)),
    createService: (payload) => act(() => api.createService(payload)),
    updateService: (service_id, payload) => act(() => api.updateService(service_id, payload)),
    deleteService: (service_id) => act(() => api.deleteService(service_id)),
    user,
    authReady,
    login,
    logout,
  };
}
