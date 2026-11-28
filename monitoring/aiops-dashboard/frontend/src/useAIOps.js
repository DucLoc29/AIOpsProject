import { useCallback, useEffect, useState } from 'react';
import { api, tokenStore } from './api.js';

/** Polling state + database snapshot từ Backend (2s/lần), quản lý phiên đăng nhập RBAC & CRUD. */
export function useAIOps(intervalMs = 2000) {
  const [state, setState] = useState(null);
  const [db, setDb] = useState(null);
  const [error, setError] = useState(null);
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);

  const forceLogout = useCallback(() => {
    tokenStore.clear();
    setUser(null);
    setState(null);
    setDb(null);
  }, []);

  const refreshMe = useCallback(async () => {
    if (!tokenStore.get()) {
      setUser(null);
      return null;
    }
    try {
      const res = await api.me();
      setUser(res.user || null);
      return res.user || null;
    } catch (e) {
      if (e.status === 401 || e.status === 403) {
        forceLogout();
      }
      return null;
    }
  }, [forceLogout]);

  // Kiểm tra token sẵn có trong localStorage khi tải trang
  useEffect(() => {
    if (!tokenStore.get()) {
      setAuthReady(true);
      return;
    }
    api
      .me()
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
        forceLogout();
      } else {
        setError(e.message);
      }
    }
  }, [forceLogout]);

  const effectivePollMs = Math.max(
    1000,
    Math.min(10000, Number(state?.settings?.ui_poll_interval_ms) || intervalMs)
  );

  // Chỉ polling khi đã xác thực thành công (tự cập nhật nhịp khi admin đổi trong Cài đặt chung)
  useEffect(() => {
    if (!user) return undefined;
    refresh();
    const t = setInterval(refresh, effectivePollMs);
    return () => clearInterval(t);
  }, [user, refresh, effectivePollMs]);

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
      // Bỏ qua lỗi mạng khi đăng xuất
    }
    forceLogout();
  }, [forceLogout]);

  const changePassword = useCallback(async (currentPassword, newPassword) => {
    const res = await api.changePassword(currentPassword, newPassword);
    if (res.token) tokenStore.set(res.token);
    if (res.user) setUser(res.user);
    return res;
  }, []);

  const act = useCallback(
    async (fn) => {
      try {
        setState(await fn());
        setDb(await api.getDatabase());
        setError(null);
      } catch (e) {
        if (e.status === 401) {
          forceLogout();
          return;
        }
        setError(e.message);
      }
    },
    [forceLogout]
  );

  return {
    state,
    db,
    error,
    user,
    authReady,
    login,
    logout,
    forceLogout,
    refreshMe,
    changePassword,
    setPolicy: (payload) => act(() => api.setPolicy(payload)),
    approve: (service_id) => act(() => api.approve(service_id)),
    dismiss: (service_id) => act(() => api.dismiss(service_id)),
    rollback: (service_id, fault_class) => act(() => api.rollback(service_id, fault_class)),
    testTelegram: () => api.testTelegram(),
    getSettings: () => api.getSettings(),
    updateSettings: async (payload) => {
      const res = await api.updateSettings(payload);
      if (res.state) setState(res.state);
      return res;
    },
    resetSettings: async () => {
      const res = await api.resetSettings();
      if (res.state) setState(res.state);
      return res;
    },
    createService: (payload) => act(() => api.createService(payload)),
    updateService: (service_id, payload) => act(() => api.updateService(service_id, payload)),
    deleteService: (service_id) => act(() => api.deleteService(service_id)),
  };
}
