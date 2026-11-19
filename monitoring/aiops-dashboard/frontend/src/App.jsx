import React, { useEffect, useState } from 'react';
import Sidebar, { NAV_ITEMS } from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import { useAIOps } from './useAIOps.js';
import OverviewPage from './pages/OverviewPage.jsx';
import ServiceDetailPage from './pages/ServiceDetailPage.jsx';
import ResponsePage from './pages/ResponsePage.jsx';
import ServicesPage from './pages/ServicesPage.jsx';

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
      <Sidebar view={view} onChange={setView} state={ctx.state} />
      <div className="main-workspace">
        <Topbar view={view} state={ctx.state} />
        <main className="content-body">
          {ctx.error && <div className="error-banner">Mất kết nối Backend: {ctx.error}</div>}
          {view === 'overview' && <OverviewPage ctx={ctx} onOpenService={openService} />}
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
        </main>
      </div>
    </div>
  );
}
