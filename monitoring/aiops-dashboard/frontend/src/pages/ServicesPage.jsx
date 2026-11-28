import React, { useMemo, useState } from 'react';
import DataTable, { Code } from '../components/DataTable.jsx';
import { StatusPill } from '../lib.jsx';

const EMPTY_FORM = {
  service_name: '',
  service_type: 'HTTP',
  address: '192.168.10.10',
  port: 8080,
  description: '',
};

export default function ServicesPage({ ctx, onOpenService }) {
  const { db, state, createService, updateService, deleteService } = ctx;
  const isAdmin = ctx.user?.role === 'ADMIN';
  const services = db?.Services || [];
  const evals = state?.service_evaluations || {};

  const [editingId, setEditingId] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('ALL');
  const [statusFilter, setStatusFilter] = useState('ALL');

  const typeOptions = useMemo(() => {
    const s = new Set(services.map((x) => String(x.service_type || '').toUpperCase()).filter(Boolean));
    return Array.from(s).sort();
  }, [services]);

  const filteredServices = useMemo(() => {
    const q = query.trim().toLowerCase();
    return services.filter((s) => {
      const sev = evals[s.service_id]?.severity_level || 'NORMAL';
      if (typeFilter !== 'ALL' && String(s.service_type || '').toUpperCase() !== typeFilter) return false;
      if (statusFilter === 'NORMAL' && sev !== 'NORMAL') return false;
      if (statusFilter === 'DEGRADED' && sev === 'NORMAL') return false;
      if (!q) return true;
      return (
        String(s.service_name || '').toLowerCase().includes(q) ||
        String(s.service_id || '').toLowerCase().includes(q) ||
        String(s.address || '').toLowerCase().includes(q) ||
        String(s.port || '').includes(q) ||
        String(s.description || '').toLowerCase().includes(q)
      );
    });
  }, [services, evals, query, typeFilter, statusFilter]);

  const startAdd = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setShowForm(true);
  };

  const startEdit = (s) => {
    setEditingId(s.service_id);
    setForm({
      service_name: s.service_name || '',
      service_type: s.service_type || 'HTTP',
      address: s.address || '192.168.10.10',
      port: s.port || 80,
      description: s.description || '',
    });
    setShowForm(true);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!form.service_name.trim()) return;
    if (editingId) await updateService(editingId, form);
    else await createService(form);
    setShowForm(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  const columns = [
    {
      key: 'service_name',
      label: 'Dịch vụ',
      render: (s) => (
        <>
          <strong>{s.service_name}</strong>
          <div className="muted">{s.description}</div>
        </>
      ),
    },
    { key: 'service_type', label: 'Loại', render: (s) => <Code>{s.service_type}</Code> },
    {
      key: 'address',
      label: 'Địa chỉ',
      render: (s) => (
        <Code>
          {s.address}:{s.port}
        </Code>
      ),
    },
    {
      key: 'status',
      label: 'Tình trạng',
      render: (s) => <StatusPill level={evals[s.service_id]?.severity_level || 'NORMAL'} />,
    },
    {
      key: 'actions',
      label: 'Thao tác',
      render: (s) => (
        <div className="row-actions">
          <button className="apple-btn active" onClick={() => onOpenService(s.service_id)}> Chi tiết </button>
          
          {isAdmin && (
            <>
              <button className="apple-btn secondary" onClick={() => startEdit(s)}> Sửa </button>
              {services.length > 1 && (
                <button className="apple-btn tone-red" onClick={() => deleteService(s.service_id)}> Xóa </button>
              )}
            </>
          )}

        </div>
      ),
    },
  ];

  return (
    <>
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Danh sách dịch vụ ({filteredServices.length}/{services.length})</h3>
          {isAdmin && (
            <button className="apple-btn active" onClick={() => (showForm ? setShowForm(false) : startAdd())}>
              {showForm ? 'Đóng' : '+ Thêm dịch vụ'}
            </button>
          )}
        </div>

        <div className="table-toolbar">
          <input
            className="apple-select table-filter-input"
            placeholder="Tìm theo tên dịch vụ, IP, cổng, mô tả..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="apple-select table-filter-select"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="ALL">Tất cả loại</option>
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            className="apple-select table-filter-select"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="ALL">Tất cả tình trạng</option>
            <option value="NORMAL">Bình thường</option>
            <option value="DEGRADED">Đang bất thường</option>
          </select>
          {(query || typeFilter !== 'ALL' || statusFilter !== 'ALL') && (
            <button
              type="button"
              className="apple-btn secondary preset-btn"
              onClick={() => {
                setQuery('');
                setTypeFilter('ALL');
                setStatusFilter('ALL');
              }}
            >
              Xóa bộ lọc
            </button>
          )}
        </div>

        {isAdmin && showForm && (
          <form className="svc-form" onSubmit={submit}>
            <label>
              <span>Tên dịch vụ *</span>
              <input
                className="apple-select"
                required
                placeholder="VD: Internal Portal"
                value={form.service_name}
                onChange={(e) => setForm({ ...form, service_name: e.target.value })}
              />
            </label>
            <label>
              <span>Loại</span>
              <select
                className="apple-select"
                value={form.service_type}
                onChange={(e) => setForm({ ...form, service_type: e.target.value })}
              >
                <option value="HTTP">HTTP</option>
                <option value="API">API</option>
                <option value="DATABASE">DATABASE</option>
                <option value="SSH">SSH</option>
                <option value="TCP">TCP</option>
              </select>
            </label>
            <label>
              <span>Máy chủ</span>
              <select
                className="apple-select"
                value={form.address}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              >
                <option value="192.168.10.10">192.168.10.10 (service-server)</option>
                <option value="192.168.10.20">192.168.10.20 (app-server)</option>
                <option value="192.168.10.21">192.168.10.21 (db-server)</option>
              </select>
            </label>
            <label>
              <span>Cổng</span>
              <input
                type="number"
                min="1"
                max="65535"
                className="apple-select"
                value={form.port}
                onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
              />
            </label>
            <label className="wide">
              <span>Mô tả</span>
              <input
                className="apple-select"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </label>
            <div className="wide form-actions">
              <button type="button" className="apple-btn secondary" onClick={() => setShowForm(false)}>
                Hủy
              </button>
              <button type="submit" className="apple-btn active">
                {editingId ? 'Lưu thay đổi' : 'Tạo dịch vụ'}
              </button>
            </div>
          </form>
        )}

        <DataTable
          columns={columns}
          rows={filteredServices}
          pageSize={8}
          resetKey={`${query}|${typeFilter}|${statusFilter}`}
          empty="Không có dịch vụ nào khớp bộ lọc."
        />
      </div>
    </>
  );
}

