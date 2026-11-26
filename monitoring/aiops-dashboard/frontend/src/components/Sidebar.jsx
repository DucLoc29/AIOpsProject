import React from 'react';
import Icon from './Icon.jsx';

export const NAV_ITEMS = [
  { id: 'overview', label: 'Tổng quan', icon: 'home' },
  { id: 'service-detail', label: 'Chi tiết dịch vụ', icon: 'chart' },
  { id: 'response', label: 'Ứng phó & Cảnh báo', icon: 'shield' },
  { id: 'services', label: 'Quản lý dịch vụ', icon: 'server' },
  { id: 'users', label: 'Quản lý tài khoản', icon: 'users', adminOnly: true },
  { id: 'settings', label: 'Cài đặt chung', icon: 'settings', adminOnly: true },
];

export default function Sidebar({ view, onChange, state, user, onLogout, onOpenChangePassword }) {
  const hostIp = window.location.hostname || 'localhost';
  const net = state?.network_state || {};
  const alertCount = (net.active_service_rules?.length || 0) + (net.pending_approvals?.length || 0);

  const visibleItems = NAV_ITEMS.filter((item) => !item.adminOnly || user?.role === 'ADMIN');

  return (
    <aside className="apple-sidebar">
      <div className="sidebar-brand">
        <div className="brand-title">AIOps Center</div>
        <div className="brand-sub">Giám sát &amp; ứng phó dịch vụ</div>
      </div>

      <nav className="sidebar-nav">
        {visibleItems.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${view === item.id ? 'active' : ''}`}
            onClick={() => onChange(item.id)}
          >
            <span className="nav-item-left">
              <Icon name={item.icon} />
              {item.label}
            </span>
            {item.id === 'response' && alertCount > 0 && <span className="nav-badge">{alertCount}</span>}
          </button>
        ))}
      </nav>

      <div className="sidebar-external">
        <div className="sidebar-section-label">Công cụ giám sát</div>
        <a className="ext-link" href={`http://${hostIp}:3000`} target="_blank" rel="noreferrer">
          <span className="nav-item-left">Grafana</span>
          <code>:3000</code>
        </a>
        <a className="ext-link" href={`http://${hostIp}:9090`} target="_blank" rel="noreferrer">
          <span className="nav-item-left">Prometheus</span>
          <code>:9090</code>
        </a>
      </div>

      {user && (
        <div className="sidebar-footer">
          <div className="sidebar-user-card">
            <div className="sidebar-user-profile">
              <div className={`user-avatar ${user.role === 'ADMIN' ? 'is-admin' : ''}`}>
                {(user.username || 'U').slice(0, 1).toUpperCase()}
              </div>
              <div className="sidebar-user-meta">
                <div className="sidebar-user-head">
                  <strong>{user.username}</strong>
                  <span className={`role-pill ${user.role === 'ADMIN' ? 'role-admin' : 'role-viewer'}`}>
                    {user.role}
                  </span>
                </div>
                {user.full_name && <div className="sidebar-user-fullname">{user.full_name}</div>}
              </div>
            </div>
            <div className="sidebar-user-actions">
              <button
                type="button"
                className="user-action-btn"
                onClick={onOpenChangePassword}
              >
                <Icon name="key" size={14} />
                <span>Đổi MK</span>
              </button>
              <button
                type="button"
                className="user-action-btn danger"
                onClick={onLogout}
              >
                <Icon name="logout" size={14} />
                <span>Đăng xuất</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
