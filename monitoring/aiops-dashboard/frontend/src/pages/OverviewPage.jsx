import React from 'react';
import { Code } from '../components/DataTable.jsx';
import { SCENARIOS, StatusPill, faultLabel, sevLabel, sevRank, worstLevel } from '../lib.jsx';

function Metric({ label, value, unit }) {
  return (
    <div className="mini-metric">
      <span>{label}</span>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
    </div>
  );
}

function ServiceCard({ ev, onOpen }) {
  const sev = ev.severity_level || 'NORMAL';
  const m = ev.metrics || {};
  const invoked = ev.model3?.invoked;
  const protectedNow = ev.protection_status === 'PROTECTED';
  const pending = ev.protection_status === 'PENDING_APPROVAL';

  return (
    <button className={`svc-card sev-border-${sev}`} onClick={() => onOpen(ev.service_id)}>
      <div className="svc-card-head">
        <div>
          <h4>{ev.service_name}</h4>
          <span className="svc-sub">
            {ev.service_type} · <Code>{ev.address}:{ev.port}</Code>
          </span>
        </div>
        <StatusPill level={sev} />
      </div>

      <div className="mini-metric-row">
        <Metric label="Độ trễ" value={m.latency ?? 0} unit=" ms" />
        <Metric label="Phản hồi" value={m.response_time ?? 0} unit=" ms" />
        <Metric label="Khả dụng" value={m.uptime ?? 100} unit="%" />
      </div>

      <div className="svc-lines">
        <div>
          <span>Dự báo 5 phút tới</span>
          <strong>
            {sevLabel(ev.model2?.forecast_level)} ({ev.model2?.prob_5m_pct?.toFixed(0) ?? 0}%)
          </strong>
        </div>
        <div>
          <span>Nguyên nhân</span>
          <strong className={invoked ? 'text-danger' : ''}>
            {invoked ? faultLabel(ev.model3.fault_class) : 'Không có sự cố'}
          </strong>
        </div>
      </div>

      {(protectedNow || pending) && (
        <div className={`svc-chip ${pending ? 'warn' : 'ok'}`}>
          {pending ? 'Chờ quản trị viên phê duyệt' : 'Đã chặn tác nhân tấn công'}
        </div>
      )}
    </button>
  );
}

export default function OverviewPage({ ctx, onOpenService }) {
  const { state, simulate, setPolicy } = ctx;
  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const worst = worstLevel(evals);
  const troubled = list.filter((e) => sevRank(e.severity_level) >= 2);
  const events = (state.audit_logs || []).slice(0, 6);

  const bannerText =
    troubled.length > 0
      ? `${troubled.length} dịch vụ đang gặp sự cố`
      : worst === 'MEDIUM'
      ? 'Có dao động nhẹ, đang theo dõi'
      : 'Tất cả dịch vụ hoạt động bình thường';

  return (
    <>
      <div className={`status-banner sev-bg-${worst}`}>
        <div>
          <h2>{bannerText}</h2>
          <p>
            Đang giám sát {list.length} dịch vụ · Cập nhật lúc {state.timestamp} · Nguồn dữ liệu:{' '}
            {state.data_source_mode === 'LIVE_PROMETHEUS' ? 'Thời gian thực (Prometheus)' : 'Mô phỏng'}
          </p>
        </div>
        <StatusPill level={worst} big />
      </div>

      <div className="section-title">
        <h3>Các dịch vụ</h3>
        <span>Bấm vào một dịch vụ để xem chi tiết</span>
      </div>
      <div className="svc-grid">
        {list.map((ev) => (
          <ServiceCard key={ev.service_id} ev={ev} onOpen={onOpenService} />
        ))}
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Mô phỏng sự cố (dùng để demo)</h3>
          <select
            className="apple-select"
            value={state.data_source_mode}
            onChange={(e) => setPolicy({ data_source_mode: e.target.value })}
          >
            <option value="LIVE_PROMETHEUS">Nguồn: Thời gian thực</option>
            <option value="SIMULATION">Nguồn: Mô phỏng</option>
          </select>
        </div>
        <div className="scenario-grid">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              className={`apple-btn ${state.scenario_active === s.id ? 'active' : ''}`}
              onClick={() => simulate(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Sự kiện gần đây</h3>
        </div>
        {events.length === 0 ? (
          <p className="muted">Chưa có sự kiện nào.</p>
        ) : (
          <ul className="event-list">
            {events.map((e, i) => (
              <li key={i}>
                <Code>{e.time || e.timestamp}</Code>
                <strong>{faultLabel(e.fault_class)}</strong>
                <span className="muted">{e.target}</span>
                <span className={`event-status st-${e.status}`}>{e.status}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

