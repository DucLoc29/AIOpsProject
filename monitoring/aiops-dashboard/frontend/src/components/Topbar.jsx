import React from 'react';
import { sevLabel, worstLevel } from '../lib.jsx';

const TITLES = {
  overview: ['Tổng quan', 'Tình trạng của tất cả dịch vụ trong một màn hình'],
  'service-detail': ['Chi tiết dịch vụ', 'Chỉ số, dự báo, nguyên nhân và xử lý của một dịch vụ'],
  response: ['Ứng phó & Cảnh báo', 'Luật chặn đang áp dụng, cảnh báo và nhật ký xử lý'],
  services: ['Quản lý dịch vụ', 'Thêm, sửa, xóa các dịch vụ được giám sát'],
  users: ['Quản lý tài khoản', 'Thêm, sửa, phân quyền và xóa tài khoản truy cập'],
};

export default function Topbar({ view, state }) {
  const [title, subtitle] = TITLES[view] || TITLES.overview;
  const worst = worstLevel(state?.service_evaluations);
  const cls = worst === 'NORMAL' ? '' : worst === 'MEDIUM' ? 'warn' : 'danger';

  return (
    <header className="apple-topbar">
      <div className="page-heading">
        <h2>{title}</h2>
        <p>{subtitle}</p>
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
