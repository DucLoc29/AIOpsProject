import React from 'react';
import DataTable, { Badge, Code } from '../components/DataTable.jsx';
import { StatusPill, faultLabel } from '../lib.jsx';

const auditColumns = [
  { key: 'time', label: 'Thời gian', render: (r) => <Code>{r.timestamp || r.time}</Code> },
  { key: 'service_id', label: 'Dịch vụ', render: (r) => <Code>{r.service_id || 'ALL'}</Code> },
  { key: 'fault_class', label: 'Chẩn đoán', render: (r) => <strong>{faultLabel(r.fault_class)}</strong> },
  { key: 'trigger', label: 'Chế độ' },
  { key: 'action', label: 'Hành động' },
  { key: 'status', label: 'Kết quả', render: (r) => <Badge value={r.status} /> },
  { key: 'actor', label: 'Người thực hiện', render: (r) => r.actor || 'SYSTEM' }
];

export default function ResponsePage({ ctx }) {
  const { state, db, approve, rollback, setPolicy } = ctx;
  const isAdmin = ctx.user?.role === 'ADMIN';
  const net = state.network_state || {};
  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const activeRules = net.active_service_rules || [];
  const pending = net.pending_approvals || [];
  const auto = state.policy_gate?.auto_pilot;

  const alertCols = [
    { key: 'created_at', label: 'Thời gian', render: (r) => <Code>{r.created_at}</Code> },
    { key: 'service_id', label: 'Dịch vụ', render: (r) => <Code>{r.service_id}</Code> },
    { key: 'alert_type', label: 'Nguyên nhân', render: (r) => <strong>{faultLabel(r.alert_type)}</strong> },
    { key: 'severity', label: 'Mức độ', render: (r) => <Badge value={r.severity} /> },
    { key: 'status', label: 'Trạng thái' },
  ];

  return (
    <>
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Chế độ xử lý</h3>
          {
            isAdmin &&  (
            <button className="apple-btn secondary" onClick={() => rollback()}>
              Gỡ chặn tất cả dịch vụ
            </button>)
          }
        </div>
        <div className="mode-row">
          <button
            className={`apple-btn ${auto ? 'active' : ''}`}
            onClick={() => setPolicy({ auto_pilot: true })}
            disabled={!isAdmin}
          >
            Tự động (Auto-Pilot)
          </button>
          
          <button
            className={`apple-btn ${!auto ? 'active' : ''}`}
            onClick={() => setPolicy({ auto_pilot: false })}
            disabled={!isAdmin}
          >
            Chờ phê duyệt (Human-in-the-Loop)
          </button>

          <span className="muted">
            {auto
              ? 'Hệ thống tự chặn ngay khi xác định được nguyên nhân.'
              : 'Hệ thống chỉ đề xuất, quản trị viên bấm phê duyệt mới thực thi.'}
          </span>
        </div>
      </div>

      {pending.map((p) => (
        <div className="apple-card pending-card" key={p.service_id}>
          <div className="card-title-row">
            <h3>Chờ phê duyệt: {p.service_name}</h3>
            <span className="apple-tag">
              {faultLabel(p.fault_class)} · {p.confidence}
            </span>
          </div>
          <p>{p.remediation.title}</p>
          <div className="code-snippet">
            <code>{p.remediation.command}</code>
          </div>
          
          {isAdmin && (
            <button className="apple-btn active" onClick={() => approve(p.service_id)}>
              Phê duyệt &amp; thực thi
            </button>
          )}

        </div>
      ))}

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Bảo vệ theo từng dịch vụ</h3>
          <span className="apple-tag">{activeRules.length} dịch vụ đang có luật chặn</span>
        </div>
        <div className="table-scroll">
          <table className="apple-table">
            <thead>
              <tr>
                <th>Dịch vụ</th>
                <th>Tình trạng</th>
                <th>Luật đang áp dụng</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list.map((ev) => {
                const rule = ev.active_rule;
                return (
                  <tr key={ev.service_id}>
                    <td>
                      <strong>{ev.service_name}</strong>
                      <div className="muted">
                        <Code>{ev.address}:{ev.port}</Code>
                      </div>
                    </td>
                    <td>
                      <StatusPill level={ev.severity_level} />
                    </td>
                    <td>
                      {rule ? (
                        <Code>{rule.command}</Code>
                      ) : (
                        <span className="muted">Không có luật chặn</span>
                      )}
                    </td>
                    <td>
                      {isAdmin && rule && (
                        <button className="apple-btn secondary" onClick={() => rollback(ev.service_id)}>
                          Gỡ chặn
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Cảnh báo</h3>
        </div>
        <DataTable columns={alertCols} rows={db?.Alerts || []} empty="Chưa có cảnh báo nào." />
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Nhật ký ứng phó</h3>
        </div>
        <DataTable columns={auditColumns} rows={state.audit_logs || []} empty="Chưa có nhật ký." />
      </div>
    </>
  );
}

