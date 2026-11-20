import React from 'react';
import PerformanceCharts from '../components/PerformanceCharts.jsx';
import DataTable, { Badge, Code } from '../components/DataTable.jsx';
import { StatusPill, faultLabel, normSev, sevLabel } from '../lib.jsx';

export default function ServiceDetailPage({ ctx, selectedService, onSelectService, onNavigate }) {
  const { state, db, approve, rollback } = ctx;
  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const ev = evals[selectedService] || list[0];

  if (!ev) return <div className="apple-card">Chưa có dịch vụ nào để hiển thị.</div>;

  const sid = ev.service_id;
  const sev = normSev(ev.severity_level);
  const forecastSev = normSev(ev.model2?.forecast_level);
  const invoked = ev.model3?.invoked;
  const activeRule = ev.active_rule;
  const pendingRule = ev.pending_rule;
  const alerts = (db?.Alerts || []).filter((a) => a.service_id === sid).slice(0, 5);

  const alertCols = [
    { key: 'created_at', label: 'Thời gian', render: (r) => <Code>{r.created_at}</Code> },
    { key: 'alert_type', label: 'Nguyên nhân', render: (r) => <strong>{faultLabel(r.alert_type)}</strong> },
    { key: 'severity', label: 'Mức độ', render: (r) => <Badge value={r.severity} /> },
    { key: 'status', label: 'Trạng thái' },
  ];

  return (
    <>
      <div className="tab-strip">
        {list.map((s) => (
          <button
            key={s.service_id}
            className={`tab-item ${s.service_id === sid ? 'active' : ''}`}
            onClick={() => onSelectService(s.service_id)}
          >
            <span className={`dot sev-dot-${s.severity_level}`} />
            {s.service_name}
          </button>
        ))}
      </div>

      <div className={`status-banner sev-bg-${sev}`}>
        <div>
          <h2>{ev.service_name}</h2>
          <p>
            {ev.service_type} · <Code>{ev.address}:{ev.port}</Code> · máy chủ {ev.host_node}
          </p>
          {ev.description && <p>{ev.description}</p>}
        </div>
        <StatusPill level={sev} big />
      </div>

      <div className="summary-3">
        <div className="apple-card summary-box">
          <span>Tình trạng hiện tại</span>
          <strong className={`sev-text-${sev}`}>{sevLabel(sev)}</strong>
          <em>Đánh giá bởi Model 1 (EIF)</em>
        </div>
        <div className="apple-card summary-box">
          <span>Dự báo 5 phút tới</span>
          <strong className={`sev-text-${forecastSev}`}>{sevLabel(forecastSev)}</strong>
          <em>Khả năng suy giảm kéo dài: {ev.model2?.prob_5m_pct?.toFixed(0) ?? 0}%</em>
        </div>
        <div className="apple-card summary-box">
          <span>Nguyên nhân sự cố</span>
          <strong className={invoked ? 'text-danger' : ''}>
            {invoked ? faultLabel(ev.model3.fault_class) : 'Không có sự cố'}
          </strong>
          <em>
            {invoked
              ? `Độ tin cậy ${ev.model3.confidence_pct?.toFixed(0)}% (Model 2 - RCA)`
              : 'Model 2 (RCA) đang ở chế độ chờ'}
          </em>
        </div>
      </div>

      <div className="apple-card action-box">
        <div className="card-title-row">
          <h3>Xử lý tự động</h3>
          <button className="apple-btn secondary" onClick={() => onNavigate('response')}>
            Xem tất cả cảnh báo &amp; ứng phó →
          </button>
        </div>
        {activeRule ? (
          <>
            <p>
              Đã chặn <strong>{activeRule.attacker_ip}</strong> truy cập vào cổng{' '}
              <strong>:{ev.port}</strong> của dịch vụ này (lúc {activeRule.applied_at}, chế độ{' '}
              {activeRule.mode}).
            </p>
            <div className="code-snippet">
              <code>{activeRule.command}</code>
            </div>
            <button className="apple-btn tone-red" onClick={() => rollback(sid)}>
              Gỡ chặn dịch vụ này
            </button>
          </>
        ) : pendingRule ? (
          <>
            <p>
              Hệ thống đề xuất: <strong>{pendingRule.remediation.title}</strong>
            </p>
            <div className="code-snippet">
              <code>{pendingRule.remediation.command}</code>
            </div>
            <button className="apple-btn active" onClick={() => approve(sid)}>
              Phê duyệt &amp; thực thi
            </button>
          </>
        ) : (
          <p className="muted">Không cần can thiệp. Dịch vụ đang được giám sát liên tục.</p>
        )}
      </div>

      <PerformanceCharts history={state.performance_history || []} selectedService={sid} />

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Cảnh báo gần đây của dịch vụ này</h3>
        </div>
        <DataTable columns={alertCols} rows={alerts} empty="Dịch vụ này chưa có cảnh báo nào." />
      </div>
    </>
  );
}
