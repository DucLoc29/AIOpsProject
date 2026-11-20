async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
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
  rollback: (service_id) => sendJson('POST', '/api/rollback', service_id ? { service_id } : {}),
  createService: (payload) => sendJson('POST', '/api/services', payload),
  updateService: (service_id, payload) => sendJson('PUT', `/api/services/${service_id}`, payload),
  deleteService: (service_id) => sendJson('DELETE', `/api/services/${service_id}`, { service_id }),
};
