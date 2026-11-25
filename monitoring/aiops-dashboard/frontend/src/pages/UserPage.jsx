import React, { useEffect, useState } from 'react';
import { api } from '../api.js';
import DataTable, { Code } from '../components/DataTable.jsx';

const EMPTY_FORM = {
  username: '',
  full_name: '',
  role: 'VIEWER',
  password: '',
};

export default function UsersPage({ ctx }) {
  const currentUsername = ctx.user?.username || '';
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [editingUsername, setEditingUsername] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [showForm, setShowForm] = useState(false);

  const loadUsers = async () => {
    try {
      const res = await api.getUsers();
      setUsers(res.Users || []);
      setErr('');
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

  const startAdd = () => {
    setEditingUsername(null);
    setForm(EMPTY_FORM);
    setErr('');
    setShowForm(true);
  };

  const startEdit = (u) => {
    setEditingUsername(u.username);
    setForm({
      username: u.username || '',
      full_name: u.full_name || '',
      role: u.role || 'VIEWER',
      password: '',
    });
    setErr('');
    setShowForm(true);
  };

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    try {
      const res = editingUsername
        ? await api.updateUser(editingUsername, {
            full_name: form.full_name,
            role: form.role,
            password: form.password,
          })
        : await api.createUser(form);
      setUsers(res.Users || []);
      setShowForm(false);
      setEditingUsername(null);
      setForm(EMPTY_FORM);
    } catch (ex) {
      setErr(ex.message);
    }
  };

  const handleDelete = async (uname) => {
    if (!window.confirm(`Xóa tài khoản "${uname}"?`)) return;
    setErr('');
    try {
      const res = await api.deleteUser(uname);
      setUsers(res.Users || []);
    } catch (ex) {
      setErr(ex.message);
    }
  };

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
      render: (u) => <span className="apple-tag">{u.role}</span>,
    },
    {
      key: 'actions',
      label: 'Thao tác',
      render: (u) => (
        <div className="row-actions">
          <button className="apple-btn secondary" onClick={() => startEdit(u)}>
            Sửa
          </button>
          {u.username !== currentUsername && (
            <button className="apple-btn tone-red" onClick={() => handleDelete(u.username)}>
              Xóa
            </button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="apple-card">
      <div className="card-title-row">
        <h3>Danh sách tài khoản ({users.length})</h3>
        <button
          className="apple-btn active"
          onClick={() => (showForm ? setShowForm(false) : startAdd())}
        >
          {showForm ? 'Đóng' : '+ Thêm tài khoản'}
        </button>
      </div>

      {err && (
        <div className="error-banner" style={{ marginBottom: 14 }}>
          {err}
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
              placeholder="vd: ops_user"
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
              onChange={(e) => setForm({ ...form, role: e.target.value })}
            >
              <option value="VIEWER">VIEWER (Chỉ đọc)</option>
              <option value="ADMIN">ADMIN (Toàn quyền)</option>
            </select>
          </label>

          <label>
            <span>{editingUsername ? 'Mật khẩu mới (để trống nếu giữ cũ)' : 'Mật khẩu *'}</span>
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
            <button type="submit" className="apple-btn active">
              {editingUsername ? 'Lưu thay đổi' : 'Tạo tài khoản'}
            </button>
          </div>
        </form>
      )}

      {loading ? (
        <p className="muted">Đang tải danh sách tài khoản...</p>
      ) : (
        <DataTable columns={columns} rows={users} empty="Chưa có tài khoản nào." />
      )}
    </div>
  );
}



