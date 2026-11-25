import React, { useEffect, useState } from 'react';
import Sidebar, { NAV_ITEMS } from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import { useAIOps } from './useAIOps.js';
import OverviewPage from './pages/OverviewPage.jsx';
import ServiceDetailPage from './pages/ServiceDetailPage.jsx';
import ResponsePage from './pages/ResponsePage.jsx';
import ServicesPage from './pages/ServicesPage.jsx';
import LoginPage from './pages/LoginPage.jsx';
import UsersPage from './pages/UsersPage.jsx';

function initialView() {
  const hash = window.location.hash.replace('#/', '');
  return NAV_ITEMS.some((n) => n.id === hash) ? hash : 'overview';
}

export default function App() {
  const [view, setView] = useState(initialView);
  const [selectedService, setSelectedService] = useState('srv_web_portal');
  const ctx = useAIOps();

  useEffect(() => {
    window.location.hash = `/${view}`;
  }, [view]);

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

  // Chặn trường hợp `VIEWER` tự gõ `#/users` trên thanh địa chỉ)
  // view !users => view | view users & admin => view | víew users & !admin => overview
  const activeView = view === 'users' && ctx.user.role !== 'ADMIN' ? 'overview' : view;

  return (
    <div className="app-layout">
      <Sidebar view={view} onChange={setView} state={ctx.state} user={ctx.user} onLogout={ctx.logout}/>
      <div className="main-workspace">
        <Topbar view={view} state={ctx.state} />
        <main className="content-body">
          {ctx.error && <div className="error-banner">Mất kết nối Backend: {ctx.error}</div>}
          {activeView === 'overview' && <OverviewPage ctx={ctx} onOpenService={openService} />}
          {view === 'service-detail' && (
            <ServiceDetailPage
              ctx={ctx}
              selectedService={selectedService}
              onSelectService={setSelectedService}
              onNavigate={setView}
            />
          )}
          {view === 'response' && <ResponsePage ctx={ctx} />}
          {view === 'services' && <ServicesPage ctx={ctx} onOpenService={openService} />}
          {view === 'users' && ctx.user.role === 'ADMIN' && <UsersPage ctx={ctx} />}
        </main>
      </div>
    </div>
  );
}
