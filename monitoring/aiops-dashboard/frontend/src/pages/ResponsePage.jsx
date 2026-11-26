import React, { useState } from 'react';
import DataTable, { Badge, Code } from '../components/DataTable.jsx';
import { StatusPill, faultLabel } from '../lib.jsx';

const auditColumns = [
  { key: 'time', label: 'Thời gian', render: (r) => <Code>{r.timestamp || r.time}</Code> },
  { key: 'service_id', label: 'Dịch vụ', render: (r) => <Code>{r.service_id || 'ALL'}</Code> },
  { key: 'fault_class', label: 'Chẩn đoán', render: (r) => <strong>{faultLabel(r.fault_class)}</strong> },
  { key: 'trigger', label: 'Chế độ' },
  { key: 'action', label: 'Hành động' },
  { key: 'status', label: 'Kết quả', render: (r) => <Badge value={r.status} /> },
  { key: 'actor', label: 'Người thực hiện', render: (r) => r.actor || 'SYSTEM' },
];

const TIER_LABELS = {
  L1: 'Tầng 1 (L1)',
  L2: 'Leo thang Tầng 2 (L2)',
  L3: 'Leo thang Tầng 3 (L3)',
};

export default function ResponsePage({ ctx }) {
  const { state, db, approve, dismiss, rollback, setPolicy, testTelegram } = ctx;
  const isAdmin = ctx.user?.role === 'ADMIN';
  const net = state.network_state || {};
  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const activeRules = net.active_service_rules || [];
  const pending = net.pending_approvals || [];
  const auto = state.policy_gate?.auto_pilot;
  const tg = state.telegram || {};
  const cfg = state.settings || {};

  const [testingTg, setTestingTg] = useState(false);
  const [tgNotice, setTgNotice] = useState(null);

  const handleTestTelegram = async () => {
    setTestingTg(true);
    setTgNotice(null);
    try {
      const res = await testTelegram();
      setTgNotice({ ok: true, text: res.message || 'Đã gửi tin thử tới Telegram.' });
    } catch (ex) {
      setTgNotice({ ok: false, text: ex.message || 'Gửi tin thử thất bại.' });
    } finally {
      setTestingTg(false);
    }
  };

  const alertCols = [
    { key: 'created_at', label: 'Thời gian', render: (r) => <Code>{r.created_at}</Code> },
    { key: 'service_id', label: 'Dịch vụ / Thiết bị', render: (r) => <Code>{r.service_id}</Code> },
    { key: 'alert_type', label: 'Nguyên nhân', render: (r) => <strong>{faultLabel(r.alert_type)}</strong> },
    { key: 'severity', label: 'Mức độ', render: (r) => <Badge value={r.severity} /> },
    { key: 'status', label: 'Trạng thái', render: (r) => <Badge value={r.status} /> },
  ];

  return (
    <>
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Chế độ xử lý</h3>
          {isAdmin ? (
            <button className="apple-btn secondary" onClick={() => rollback()}>
              Gỡ chặn tất cả dịch vụ
            </button>
          ) : (
            <span className="role-pill role-viewer">Chế độ chỉ đọc (VIEWER)</span>
          )}
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
              : 'Hệ thống chỉ đề xuất, quản trị viên bấm phê duyệt mới thực thi (có leo thang L1 → L2 → L3).'}
          </span>
        </div>
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>Telegram ChatOps &amp; Cảnh báo Leo thang</h3>
          <span className={`tg-pill ${tg.enabled ? 'on' : 'off'}`}>
            {tg.enabled
              ? `Đã kết nối${tg.bot_username ? ` (${tg.bot_username})` : ''}`
              : 'Chưa cấu hình (.env)'}
          </span>
        </div>
        <div className="tg-policy-strip">
          <span><strong>Tầng 0:</strong> Lọc TransientSpike (0 tin)</span>
          <span><strong>Tầng 1:</strong> Báo ngay (0s)</span>
          <span><strong>Tầng 2:</strong> Leo thang ({tg.l2_seconds || 30}s)</span>
          <span>
            <strong>Tầng 3:</strong>{' '}
            {cfg.l3_auto_mitigate === false
              ? `Chỉ báo thủ công (${tg.l3_seconds || 60}s)`
              : `Tự bảo vệ (${tg.l3_seconds || 60}s, ≥${cfg.l3_min_confidence_pct || 80}%)`}
          </span>
          <span>
            <strong>Auto-Resolved:</strong> {cfg.auto_resolved_cycles || 2} chu kỳ NORMAL
          </span>
        </div>
        <div className="tg-footer-row">
          <span className="muted">
            {tg.enabled
              ? `Long Polling đang chạy · Đã gửi ${tg.sent_count || 0} thông báo · Gần nhất: ${tg.last_sent_at || '-'}`
              : 'Điền TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID trong .env rồi khởi động lại server để nhận cảnh báo & phê duyệt ngay trên điện thoại.'}
          </span>
          {isAdmin && tg.enabled && (
            <button
              className="apple-btn secondary"
              onClick={handleTestTelegram}
              disabled={testingTg}
            >
              {testingTg ? 'Đang gửi...' : 'Gửi tin thử Telegram'}
            </button>
          )}
        </div>
        {tg.last_error && (
          <div className="error-banner" style={{ marginTop: 10 }}>
            Lỗi Telegram gần nhất: {tg.last_error}
          </div>
        )}
        {tgNotice && (
          <div
            className={tgNotice.ok ? 'success-banner' : 'error-banner'}
            style={{ marginTop: 10 }}
          >
            {tgNotice.text}
          </div>
        )}
      </div>

      {pending.map((p) => {
        const tier = p.escalation_tier || 'L1';
        return (
          <div className="apple-card pending-card" key={p.service_id}>
            <div className="card-title-row">
              <h3>Chờ phê duyệt: {p.service_name}</h3>
              <div className="pending-badges">
                <span className={`tier-pill tier-${tier}`}>{TIER_LABELS[tier] || tier}</span>
                <span className="apple-tag">
                  {faultLabel(p.fault_class)} · {p.confidence}
                </span>
              </div>
            </div>
            <p>{p.remediation.title}</p>
            <div className="code-snippet">
              <code>{p.remediation.command}</code>
            </div>

            {isAdmin && (
              <div className="pending-actions">
                <button className="apple-btn active" onClick={() => approve(p.service_id)}>
                  Phê duyệt &amp; thực thi
                </button>
                <button className="apple-btn secondary" onClick={() => dismiss(p.service_id)}>
                  Bỏ qua
                </button>
              </div>
            )}
          </div>
        );
      })}

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
          <h3>Cảnh báo (AI &amp; Hạ tầng Alertmanager)</h3>
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
