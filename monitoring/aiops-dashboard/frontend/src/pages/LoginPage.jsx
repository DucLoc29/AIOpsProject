import React, { useState } from 'react';
import Icon from '../components/Icon.jsx';

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErr('');
    setBusy(true);
    try {
      await onLogin(username.trim(), password);
    } catch (ex) {
      setErr(ex.message || 'Đăng nhập thất bại');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-shell">
      <form className="apple-card login-card" onSubmit={handleSubmit}>
        <div className="login-brand">
          <div className="login-logo">
            <Icon name="shield" size={24} />
          </div>
          <div>
            <div className="brand-title">AIOps Center</div>
            <div className="brand-sub">Giám sát bất thường &amp; tự động ứng phó mạng</div>
          </div>
        </div>

        {err && <div className="error-banner">{err}</div>}

        <label className="policy-field">
          <span>Tên đăng nhập</span>
          <input
            className="apple-select"
            required
            autoFocus
            autoComplete="username"
            placeholder="Nhập tên tài khoản"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        <label className="policy-field">
          <span>Mật khẩu</span>
          <div className="pw-wrap">
            <input
              type={showPw ? 'text' : 'password'}
              className="apple-select"
              required
              autoComplete="current-password"
              placeholder="Nhập mật khẩu"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="pw-toggle"
              onClick={() => setShowPw((v) => !v)}
              tabIndex={-1}
            >
              {showPw ? 'Ẩn' : 'Hiện'}
            </button>
          </div>
        </label>

        <button type="submit" className="apple-btn active full" disabled={busy}>
          {busy ? 'Đang xác thực...' : 'Đăng nhập'}
        </button>

        <div className="login-foot">
          Xác thực phiên HMAC-SHA256 · Phân quyền RBAC (ADMIN / VIEWER)
        </div>
      </form>
    </div>
  );
}
