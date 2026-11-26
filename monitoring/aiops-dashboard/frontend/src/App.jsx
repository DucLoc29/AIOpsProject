import React, { useEffect, useState } from 'react';
import Sidebar, { NAV_ITEMS } from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import { useAIOps } from './useAIOps.js';
import OverviewPage from './pages/OverviewPage.jsx';
import ServiceDetailPage from './pages/ServiceDetailPage.jsx';
import ResponsePage from './pages/ResponsePage.jsx';
import ServicesPage from './pages/ServicesPage.jsx';
import LoginPage from './pages/LoginPage.jsx';
import UsersPage from './pages/UserPage.jsx';

function parseHashView() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return NAV_ITEMS.some((n) => n.id === hash) ? hash : 'overview';
}

function ChangePasswordModal({ onClose, onSubmit }) {
  const [curPw, setCurPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErr('');
    setOkMsg('');
    if (newPw.length < 4) {
      setErr('Mật khẩu mới phải có ít nhất 4 ký tự');
      return;
    }
    if (newPw !== confirmPw) {
      setErr('Xác nhận mật khẩu mới chưa khớp');
      return;
    }
    setBusy(true);
    try {
      await onSubmit(curPw, newPw);
      setOkMsg('Đã đổi mật khẩu thành công!');
      setCurPw('');
      setNewPw('');
      setConfirmPw('');
      setTimeout(onClose, 900);
    } catch (ex) {
      setErr(ex.message || 'Không thể đổi mật khẩu');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="apple-card modal-card" onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()}>
        <div className="card-title-row">
          <h3>Đổi mật khẩu cá nhân</h3>
          <button type="button" className="apple-btn secondary" onClick={onClose}>
            Đóng
          </button>
        </div>

        {err && <div className="error-banner">{err}</div>}
        {okMsg && <div className="success-banner">{okMsg}</div>}

        <label className="policy-field">
          <span>Mật khẩu hiện tại</span>
          <input
            type="password"
            className="apple-select"
            required
            autoFocus
            value={curPw}
            onChange={(e) => setCurPw(e.target.value)}
          />
        </label>

        <label className="policy-field">
          <span>Mật khẩu mới</span>
          <input
            type="password"
            className="apple-select"
            required
            minLength={4}
            placeholder="Ít nhất 4 ký tự"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
          />
        </label>

        <label className="policy-field">
          <span>Nhập lại mật khẩu mới</span>
          <input
            type="password"
            className="apple-select"
            required
            minLength={4}
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
          />
        </label>

        <div className="form-actions">
          <button type="button" className="apple-btn secondary" onClick={onClose}>
            Hủy
          </button>
          <button type="submit" className="apple-btn active" disabled={busy}>
            {busy ? 'Đang lưu...' : 'Cập nhật mật khẩu'}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState(parseHashView);
  const [selectedService, setSelectedService] = useState('srv_web_portal');
  const [showPwModal, setShowPwModal] = useState(false);
  const ctx = useAIOps();

  // Đồng bộ khi người dùng dùng nút Back/Forward hoặc tự sửa URL hash
  useEffect(() => {
    const onHashChange = () => {
      const next = parseHashView();
      setView(next);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const activeView = ctx.user && view === 'users' && ctx.user.role !== 'ADMIN' ? 'overview' : view;

  useEffect(() => {
    if (!ctx.authReady) return;
    if (!ctx.user) {
      if (window.location.hash !== '#/login') window.location.hash = '/login';
      return;
    }
    const desired = `#/${activeView}`;
    if (window.location.hash !== desired) window.location.hash = `/${activeView}`;
  }, [ctx.authReady, ctx.user, activeView]);

  if (!ctx.authReady) {
    return <div className="boot-screen">Đang kiểm tra phiên đăng nhập...</div>;
  }
  if (!ctx.user) {
    return <LoginPage onLogin={ctx.login} />;
  }

  if (!ctx.state) {
    return (
      <div className="boot-screen">
        {ctx.error ? `Không kết nối được Backend: ${ctx.error}` : 'Đang tải AIOps Center...'}
      </div>
    );
  }

  const openService = (id) => {
    setSelectedService(id);
    setView('service-detail');
  };

  return (
    <div className="app-layout">
      <Sidebar
        view={activeView}
        onChange={setView}
        state={ctx.state}
        user={ctx.user}
        onLogout={ctx.logout}
        onOpenChangePassword={() => setShowPwModal(true)}
      />
      <div className="main-workspace">
        <Topbar view={activeView} state={ctx.state} />
        <main className="content-body">
          {ctx.error && <div className="error-banner">Mất kết nối Backend: {ctx.error}</div>}
          {activeView === 'overview' && <OverviewPage ctx={ctx} onOpenService={openService} />}
          {activeView === 'service-detail' && (
            <ServiceDetailPage
              ctx={ctx}
              selectedService={selectedService}
              onSelectService={setSelectedService}
              onNavigate={setView}
            />
          )}
          {activeView === 'response' && <ResponsePage ctx={ctx} />}
          {activeView === 'services' && <ServicesPage ctx={ctx} onOpenService={openService} />}
          {activeView === 'users' && ctx.user.role === 'ADMIN' && <UsersPage ctx={ctx} />}
        </main>
      </div>
      {showPwModal && (
        <ChangePasswordModal
          onClose={() => setShowPwModal(false)}
          onSubmit={ctx.changePassword}
        />
      )}
    </div>
  );
}
