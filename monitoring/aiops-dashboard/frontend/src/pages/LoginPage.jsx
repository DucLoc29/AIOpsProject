import React, { useState } from 'react';

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
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
    <div className="boot-screen">
      <form
        className="apple-card"
        onSubmit={handleSubmit}
        style={{ width: '100%', maxWidth: 380, display: 'flex', flexDirection: 'column', gap: 14 }}
      >
        <div>
          <div className="brand-title">AIOps Center</div>
          <div className="brand-sub">Đăng nhập hệ thống giám sát &amp; ứng phó</div>
        </div>

        {err && <div className="error-banner">{err}</div>}

        <label className="policy-field">
          <span>Tên đăng nhập</span>
          <input
            className="apple-select"
            required
            autoFocus
            placeholder="admin hoặc viewer"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        <label className="policy-field">
          <span>Mật khẩu</span>
          <input
            type="password"
            className="apple-select"
            required
            placeholder="Nhập mật khẩu"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        <button type="submit" className="apple-btn active full" disabled={busy}>
          {busy ? 'Đang xác thực...' : 'Đăng nhập'}
        </button>
      </form>
    </div>
  );
}