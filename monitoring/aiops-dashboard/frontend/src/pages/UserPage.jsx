import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api.js';
import DataTable, { Code } from '../components/DataTable.jsx';

const EMPTY_FORM = {
  username: '',
  full_name: '',
  role: 'VIEWER',
  status: 'ACTIVE',
  password: '',
};

export default function UsersPage({ ctx }) {
  const currentUsername = ctx.user?.username || '';
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [query, setQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState('ALL');
  const [editingUsername, setEditingUsername] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);
  const [saving, setSaving] = useState(false);

  const handleApiError = (ex) => {
    if (ex?.status === 401) {
      ctx.forceLogout();
      return;
    }
    setErr(ex?.message || 'Thao tác thất bại');
  };

  const loadUsers = async () => {
    try {
      const res = await api.getUsers();
      setUsers(res.Users || []);
      setErr('');
    } catch (ex) {
      handleApiError(ex);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const stats = useMemo(() => {
    const total = users.length;
    const admins = users.filter((u) => u.role === 'ADMIN').length;
    const viewers = users.filter((u) => u.role !== 'ADMIN').length;
    const locked = users.filter((u) => u.status === 'LOCKED').length;
    return { total, admins, viewers, locked };
  }, [users]);

  const filteredUsers = useMemo(() => {
    const q = query.trim().toLowerCase();
    return users.filter((u) => {
      if (roleFilter === 'ADMIN' && u.role !== 'ADMIN') return false;
      if (roleFilter === 'VIEWER' && u.role !== 'VIEWER') return false;
      if (roleFilter === 'LOCKED' && u.status !== 'LOCKED') return false;
      if (!q) return true;
      return (
        String(u.username || '').toLowerCase().includes(q) ||
        String(u.full_name || '').toLowerCase().includes(q)
      );
    });
  }, [users, query, roleFilter]);

  const startAdd = () => {
    setEditingUsername(null);
    setForm(EMPTY_FORM);
    setErr('');
    setSuccessMsg('');
    setShowForm(true);
  };

  const startEdit = (u) => {
    setEditingUsername(u.username);
    setForm({
      username: u.username || '',
      full_name: u.full_name || '',
      role: u.role || 'VIEWER',
      status: u.status || 'ACTIVE',
      password: '',
    });
    setErr('');
    setSuccessMsg('');
    setShowForm(true);
  };

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    setSuccessMsg('');
    setSaving(true);
    try {
      if (editingUsername) {
        const res = await api.updateUser(editingUsername, {
          full_name: form.full_name,
          role: form.role,
          status: form.status,
          password: form.password,
        });
        setUsers(res.Users || []);
        setSuccessMsg(`Đã cập nhật tài khoản "${editingUsername}"`);
        if (editingUsername === currentUsername) {
          await ctx.refreshMe();
        }
      } else {
        const res = await api.createUser(form);
        setUsers(res.Users || []);
        setSuccessMsg(`Đã tạo tài khoản "${res.user?.username || form.username}"`);
      }
      setShowForm(false);
      setEditingUsername(null);
      setForm(EMPTY_FORM);
    } catch (ex) {
      handleApiError(ex);
    } finally {
      setSaving(false);
    }
  };

  const handleToggleLock = async (u) => {
    const nextStatus = u.status === 'LOCKED' ? 'ACTIVE' : 'LOCKED';
    const actionText = nextStatus === 'LOCKED' ? 'khóa' : 'mở khóa';
    if (!window.confirm(`Xác nhận ${actionText} tài khoản "${u.username}"?`)) return;
    setErr('');
    setSuccessMsg('');
    try {
      const res = await api.updateUser(u.username, {
        full_name: u.full_name,
        role: u.role,
        status: nextStatus,
      });
      setUsers(res.Users || []);
      setSuccessMsg(`Đã ${actionText} tài khoản "${u.username}"`);
    } catch (ex) {
      handleApiError(ex);
    }
  };

  const handleDelete = async (uname) => {
    if (!window.confirm(`Xóa vĩnh viễn tài khoản "${uname}"?`)) return;
    setErr('');
    setSuccessMsg('');
    try {
      const res = await api.deleteUser(uname);
      setUsers(res.Users || []);
      setSuccessMsg(`Đã xóa tài khoản "${uname}"`);
    } catch (ex) {
      handleApiError(ex);
    }
  };

  const editingSelf = Boolean(editingUsername && editingUsername === currentUsername);

  const columns = [
    {
      key: 'username',
      label: 'Tên đăng nhập',
      render: (u) => (
        <>
          <Code>{u.username}</Code>
          {u.username === currentUsername && <span className="muted"> (Bạn)</span>}
        </>
      ),
    },
    { key: 'full_name', label: 'Họ tên / Mô tả' },
    {
      key: 'role',
      label: 'Vai trò',
      render: (u) => (
        <span className={`role-pill ${u.role === 'ADMIN' ? 'role-admin' : 'role-viewer'}`}>
          {u.role}
        </span>
      ),
    },
    {
      key: 'status',
      label: 'Trạng thái',
      render: (u) => (
        <span className={`status-badge ${u.status === 'LOCKED' ? 'st-locked' : 'st-active'}`}>
          {u.status === 'LOCKED' ? 'Đã khóa' : 'Hoạt động'}
        </span>
      ),
    },
    {
      key: 'last_login',
      label: 'Đăng nhập gần nhất',
      render: (u) => <span className="muted">{u.last_login && u.last_login !== '-' ? u.last_login : 'Chưa đăng nhập'}</span>,
    },
    {
      key: 'actions',
      label: 'Thao tác',
      render: (u) => {
        const isSelf = u.username === currentUsername;
        return (
          <div className="row-actions">
            <button className="apple-btn secondary" onClick={() => startEdit(u)}>
              Sửa
            </button>
            {!isSelf && (
              <button className="apple-btn secondary" onClick={() => handleToggleLock(u)}>
                {u.status === 'LOCKED' ? 'Mở khóa' : 'Khóa'}
              </button>
            )}
            {!isSelf && (
              <button className="apple-btn tone-red" onClick={() => handleDelete(u.username)}>
                Xóa
              </button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <>
      <div className="user-stats-4">
        <div className="apple-card summary-box">
          <span>Tổng tài khoản</span>
          <strong>{stats.total}</strong>
          <em>Lưu trên MongoDB Atlas</em>
        </div>
        <div className="apple-card summary-box">
          <span>Quản trị viên (ADMIN)</span>
          <strong style={{ color: 'var(--apple-blue)' }}>{stats.admins}</strong>
          <em>Toàn quyền điều khiển &amp; CRUD</em>
        </div>
        <div className="apple-card summary-box">
          <span>Giám sát viên (VIEWER)</span>
          <strong className="sev-text-NORMAL">{stats.viewers}</strong>
          <em>Chỉ xem giám sát &amp; nhật ký</em>
        </div>
        <div className="apple-card summary-box">
          <span>Tài khoản bị khóa</span>
          <strong className={stats.locked > 0 ? 'sev-text-CRITICAL' : ''}>{stats.locked}</strong>
          <em>Chặn đăng nhập tức thì</em>
        </div>
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Danh sách tài khoản ({filteredUsers.length}/{users.length})</h3>
          <button
            className="apple-btn active"
            onClick={() => (showForm ? setShowForm(false) : startAdd())}
          >
            {showForm ? 'Đóng' : '+ Thêm tài khoản'}
          </button>
        </div>

        <div className="user-toolbar">
          <input
            className="apple-select user-search"
            placeholder="Tìm theo tên đăng nhập hoặc họ tên..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="row-actions">
            {[
              { id: 'ALL', label: 'Tất cả' },
              { id: 'ADMIN', label: 'ADMIN' },
              { id: 'VIEWER', label: 'VIEWER' },
              { id: 'LOCKED', label: 'Đã khóa' },
            ].map((f) => (
              <button
                key={f.id}
                type="button"
                className={`apple-btn ${roleFilter === f.id ? 'active' : 'secondary'}`}
                onClick={() => setRoleFilter(f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {err && (
          <div className="error-banner" style={{ marginBottom: 14 }}>
            {err}
          </div>
        )}
        {successMsg && (
          <div className="success-banner" style={{ marginBottom: 14 }}>
            {successMsg}
          </div>
        )}

        {showForm && (
          <form className="svc-form" onSubmit={submit}>
            <label>
              <span>Tên đăng nhập *</span>
              <input
                className="apple-select"
                required
                disabled={Boolean(editingUsername)}
                placeholder="vd: ops_user (3–32 ký tự)"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
              />
            </label>

            <label>
              <span>Họ tên / Mô tả</span>
              <input
                className="apple-select"
                placeholder="vd: Kỹ sư trực NOC"
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </label>

            <label>
              <span>Vai trò</span>
              <select
                className="apple-select"
                value={form.role}
                disabled={editingSelf}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                <option value="VIEWER">VIEWER (Chỉ đọc)</option>
                <option value="ADMIN">ADMIN (Toàn quyền)</option>
              </select>
            </label>

            <label>
              <span>Trạng thái</span>
              <select
                className="apple-select"
                value={form.status}
                disabled={editingSelf}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                <option value="ACTIVE">Hoạt động (ACTIVE)</option>
                <option value="LOCKED">Khóa tài khoản (LOCKED)</option>
              </select>
            </label>

            <label className="wide">
              <span>{editingUsername ? 'Đặt mật khẩu mới (để trống nếu giữ nguyên)' : 'Mật khẩu *'}</span>
              <input
                type="password"
                className="apple-select"
                required={!editingUsername}
                minLength={editingUsername ? 0 : 4}
                placeholder={editingUsername ? 'Để trống nếu không đổi' : 'Ít nhất 4 ký tự'}
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </label>

            <div className="wide form-actions">
              <button
                type="button"
                className="apple-btn secondary"
                onClick={() => setShowForm(false)}
              >
                Hủy
              </button>
              <button type="submit" className="apple-btn active" disabled={saving}>
                {saving ? 'Đang lưu...' : editingUsername ? 'Lưu thay đổi' : 'Tạo tài khoản'}
              </button>
            </div>
          </form>
        )}

        {loading ? (
          <p className="muted">Đang tải danh sách tài khoản...</p>
        ) : (
          <DataTable columns={columns} rows={filteredUsers} empty="Không có tài khoản nào khớp bộ lọc." />
        )}
      </div>
    </>
  );
}
