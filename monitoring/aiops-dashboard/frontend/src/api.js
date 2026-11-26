const TOKEN_KEY = 'aiops_token';

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY) || '',
  set: (token) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function request(path, options = {}) {
  const token = tokenStore.get();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    if (res.status === 401 && path !== '/api/auth/login') {
      tokenStore.clear();
    }
    const err = new Error(data.error || `${path} -> ${res.status}`);
    err.status = res.status;
    throw err;
  }

  return data;
}

const sendJson = (method, path, body) =>
  request(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });

export const api = {
  getState: () => request('/api/state'),
  getDatabase: () => request('/api/database'),
  setPolicy: (payload) => sendJson('POST', '/api/policy', payload),
  approve: (service_id) => sendJson('POST', '/api/approve', service_id ? { service_id } : {}),
  dismiss: (service_id) => sendJson('POST', '/api/dismiss', service_id ? { service_id } : {}),
  rollback: (service_id) => sendJson('POST', '/api/rollback', service_id ? { service_id } : {}),
  testTelegram: () => sendJson('POST', '/api/telegram/test', {}),
  createService: (payload) => sendJson('POST', '/api/services', payload),
  updateService: (service_id, payload) => sendJson('PUT', `/api/services/${service_id}`, payload),
  deleteService: (service_id) => sendJson('DELETE', `/api/services/${service_id}`, { service_id }),
  login: (username, password) => sendJson('POST', '/api/auth/login', { username, password }),
  logout: () => sendJson('POST', '/api/auth/logout', {}),
  me: () => request('/api/auth/me'),
  changePassword: (current_password, new_password) =>
    sendJson('POST', '/api/auth/change-password', { current_password, new_password }),
  getUsers: () => request('/api/users'),
  createUser: (payload) => sendJson('POST', '/api/users', payload),
  updateUser: (username, payload) => sendJson('PUT', `/api/users/${encodeURIComponent(username)}`, payload),
  deleteUser: (username) => sendJson('DELETE', `/api/users/${encodeURIComponent(username)}`, { username }),
};
