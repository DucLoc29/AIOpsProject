import React, { useState } from 'react';
import DataTable, { Code } from '../components/DataTable.jsx';
import { StatusPill } from '../lib.jsx';

const EMPTY_FORM = {
  service_name: '',
  service_type: 'HTTP',
  address: '192.168.10.10',
  port: 8080,
  check_interval: '2s',
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
      check_interval: s.check_interval || '2s',
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
          <h3>Danh sách dịch vụ ({services.length})</h3>
          {isAdmin && (
            <button className="apple-btn active" onClick={() => (showForm ? setShowForm(false) : startAdd())}>
              {showForm ? 'Đóng' : '+ Thêm dịch vụ'}
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
            <label>
              <span>Chu kỳ thu thập</span>
              <select
                className="apple-select"
                value={form.check_interval}
                onChange={(e) => setForm({ ...form, check_interval: e.target.value })}
              >
                <option value="2s">2 giây</option>
                <option value="5s">5 giây</option>
                <option value="10s">10 giây</option>
              </select>
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

        <DataTable columns={columns} rows={services} empty="Chưa có dịch vụ nào." />
      </div>
    </>
  );
}

