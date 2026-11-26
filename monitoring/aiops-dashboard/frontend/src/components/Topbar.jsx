import React from 'react';
import { sevLabel, worstLevel } from '../lib.jsx';

const TITLES = {
  overview: 'Tổng quan',
  'service-detail': 'Chi tiết dịch vụ',
  response: 'Ứng phó & Cảnh báo',
  services: 'Quản lý dịch vụ',
  users: 'Quản lý tài khoản',
  settings: 'Cài đặt chung',
};

export default function Topbar({ view, state }) {
  const title = TITLES[view] || TITLES.overview;
  const worst = worstLevel(state?.service_evaluations);
  const cls = worst === 'NORMAL' ? '' : worst === 'MEDIUM' ? 'warn' : 'danger';

  return (
    <header className="apple-topbar">
      <div className="page-heading">
        <h2>{title}</h2>
      </div>
      <div className="topbar-badges">
        <div className={`apple-pill ${cls}`}>
          <span className="dot-indicator" />
          Hệ thống: {sevLabel(worst)}
        </div>
      </div>
    </header>
  );
}
