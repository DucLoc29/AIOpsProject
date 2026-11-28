import React, { useMemo, useState } from 'react';
import PerformanceCharts from '../components/PerformanceCharts.jsx';
import DataTable, { Badge, Code } from '../components/DataTable.jsx';
import { StatusPill, faultLabel, getRuleCommands, normSev, sevLabel } from '../lib.jsx';

export default function ServiceDetailPage({ ctx, selectedService, onSelectService, onNavigate }) {
  const { state, db, approve, dismiss, rollback } = ctx;
  const isAdmin = ctx.user?.role === 'ADMIN';
  const [alertQuery, setAlertQuery] = useState('');
  const [alertSev, setAlertSev] = useState('ALL');
  const [alertStatus, setAlertStatus] = useState('ALL');

  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const ev = evals[selectedService] || list[0];
  const sid = ev?.service_id || '';

  const serviceAlerts = useMemo(
    () => (sid ? (db?.Alerts || []).filter((a) => a.service_id === sid) : []),
    [db?.Alerts, sid]
  );
  const filteredAlerts = useMemo(() => {
    const q = alertQuery.trim().toLowerCase();
    return serviceAlerts.filter((a) => {
      if (alertSev !== 'ALL' && String(a.severity || '').toLowerCase() !== alertSev) return false;
      if (alertStatus !== 'ALL' && String(a.status || '').toUpperCase() !== alertStatus) return false;
      if (!q) return true;
      return (
        String(a.alert_type || '').toLowerCase().includes(q) ||
        String(faultLabel(a.alert_type) || '').toLowerCase().includes(q) ||
        String(a.created_at || '').toLowerCase().includes(q) ||
        String(a.message || '').toLowerCase().includes(q)
      );
    });
  }, [serviceAlerts, alertQuery, alertSev, alertStatus]);

  if (!ev) return <div className="apple-card">Chưa có dịch vụ nào để hiển thị.</div>;

  const sev = normSev(ev.severity_level);
  const forecastSev = normSev(ev.model2?.forecast_level);
  const invoked = ev.model3?.invoked;
  const activeRule = ev.active_rule;
  const pendingRule = ev.pending_rule;
  const alertRule = ev.alert_rule || {};
  const modeLabelMap = {
    AUTO_PILOT: 'Tự động ứng phó',
    HUMAN_APPROVAL: 'Chờ phê duyệt',
  };

  const alertCols = [
    { key: 'created_at', label: 'Thời gian', render: (r) => <Code>{r.created_at}</Code> },
    { key: 'alert_type', label: 'Nguyên nhân', render: (r) => <strong>{faultLabel(r.alert_type)}</strong> },
    { key: 'severity', label: 'Mức độ', render: (r) => <Badge value={r.severity} /> },
    { key: 'status', label: 'Trạng thái' },
  ];

  const m1 = ev.model1 || {};
  const m1Th = m1.thresholds_4level || {};
  const m2 = ev.model2 || {};
  const m3 = ev.model3 || {};

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
            {alertRule.has_custom_rule && (
              <>
                {' · '}
                <strong>
                  {alertRule.is_active === false
                    ? 'Luật riêng: Tắt cảnh báo'
                    : `Luật riêng · ${modeLabelMap[alertRule.effective_response_mode] || 'Tùy chỉnh'}`}
                </strong>
              </>
            )}
          </p>
          {ev.description && <p>{ev.description}</p>}
        </div>
        <StatusPill level={sev} big />
      </div>

      <div className="summary-3">
        <div className="apple-card summary-box">
          <span>Tình trạng hiện tại · Model 1 (EIF)</span>
          <strong className={`sev-text-${sev}`}>{sevLabel(sev)}</strong>
          <div className="summary-metric-line">
            Điểm EIF hiện tại: <b>{Number(m1.anomaly_score ?? 0).toFixed(4)}</b>
          </div>
          <div className="summary-threshold-line">
            <span>Ngưỡng:</span>
            <span className="th-item">Trung bình ≥ <b>{m1Th.MEDIUM ?? 0.5451}</b></span>
            <span className="th-sep">·</span>
            <span className="th-item">Cao ≥ <b>{m1Th.HIGH ?? 0.6061}</b></span>
            <span className="th-sep">·</span>
            <span className="th-item">Nghiêm trọng ≥ <b>{m1Th.CRITICAL ?? 0.6709}</b></span>
          </div>
        </div>
        <div className="apple-card summary-box">
          <span>Dự báo 5 phút tới · Công thức chuỗi thời gian</span>
          <strong className={`sev-text-${forecastSev}`}>{sevLabel(forecastSev)}</strong>
          <div className="summary-metric-line">
            Tỉ lệ suy giảm: <b>{Number(m2.prob_5m_pct ?? 0).toFixed(1)}%</b> · Điểm dự báo:{' '}
            <b>{Number(m2.forecast_score ?? 0).toFixed(4)}</b>
          </div>
          <div className="summary-threshold-line">
            <span>Ngưỡng:</span>
            <span className="th-item">Kích hoạt M2 ≥ <b>{m2.threshold_pct ?? 60}%</b></span>
            <span className="th-sep">·</span>
            <span className="th-item">Nghiêm trọng ≥ <b>{m2.critical_pct ?? 80}%</b></span>
          </div>
        </div>
        <div className="apple-card summary-box">
          <span>Nguyên nhân sự cố · Model 2 (XGBoost RCA)</span>
          <strong className={invoked ? 'text-danger' : ''}>
            {invoked ? faultLabel(m3.fault_class) : 'Không có sự cố'}
          </strong>
          <div className="summary-metric-line">
            {invoked ? (
              <>
                Độ tin cậy hiện tại: <b>{Number(m3.confidence_pct ?? 0).toFixed(1)}%</b>
              </>
            ) : (
              <>
                Độ tin cậy hiện tại: <b>0.0%</b> (đang chờ)
              </>
            )}
          </div>
          <div className="summary-threshold-line">
            <span className="th-item">Ngưỡng tối thiểu: ≥ <b>{m3.confidence_threshold_pct ?? 70}%</b></span>
          </div>
        </div>
      </div>

      <div className="apple-card action-box">
        {(() => {
          const effMode =
            alertRule.effective_response_mode ||
            (ev.policy_gate?.auto_pilot ? 'AUTO_PILOT' : 'HUMAN_APPROVAL');
          const activeItems = activeRule
            ? (Array.isArray(activeRule.rules) && activeRule.rules.length > 0
                ? activeRule.rules
                : [
                    {
                      fault_class: activeRule.fault_class,
                      attacker_ip: activeRule.attacker_ip,
                      commands: getRuleCommands(activeRule),
                      command: activeRule.command,
                      applied_at: activeRule.applied_at,
                      mode: activeRule.mode,
                    },
                  ])
            : [];

          const actionRows = [];
          if (pendingRule) {
            const pRem = pendingRule.remediation || {};
            actionRows.push({
              id: `pending-${pendingRule.fault_class}`,
              kind: 'PENDING',
              fault_class: pendingRule.fault_class,
              sub_meta: `Đề xuất · Độ tin cậy ${pendingRule.confidence || '-'}`,
              attacker_ip: pRem.attacker_ip || '-',
              tier: pendingRule.escalation_tier || 'L1',
              time: pendingRule.created_at || '-',
              commands: getRuleCommands(pRem),
              title: pRem.title || '',
            });
          }
          activeItems.forEach((it, idx) => {
            actionRows.push({
              id: `active-${it.fault_class}-${idx}`,
              kind: 'ACTIVE',
              fault_class: it.fault_class,
              sub_meta: it.mode || activeRule?.mode || 'AUTO-PILOT',
              attacker_ip: it.attacker_ip || '-',
              time: it.applied_at || activeRule?.applied_at || '-',
              commands: getRuleCommands(it),
              title: '',
            });
          });

          const actionCols = [
            {
              key: 'fault_class',
              label: 'Loại sự cố',
              render: (r) => (
                <div className="rule-type-cell">
                  <strong>{faultLabel(r.fault_class)}</strong>
                  <span className="muted">{r.sub_meta}</span>
                </div>
              ),
            },
            {
              key: 'attacker_ip',
              label: 'Nguồn tấn công',
              render: (r) => <Code>{r.attacker_ip}</Code>,
            },
            {
              key: 'kind',
              label: 'Trạng thái',
              render: (r) =>
                r.kind === 'PENDING' ? (
                  <span className="tg-pill warn">
                    {r.tier && r.tier !== 'L1' ? `Chờ phê duyệt · ${r.tier}` : 'Chờ phê duyệt'}
                  </span>
                ) : (
                  <span className="tg-pill on">Đang chặn</span>
                ),
            },
            {
              key: 'time',
              label: 'Thời gian',
              render: (r) => <Code>{r.time}</Code>,
            },
            {
              key: 'commands',
              label: 'Lệnh ứng phó',
              render: (r) => (
                <div className="rule-cmd-stack">
                  {r.commands.length > 0 ? (
                    r.commands.map((c, idx) => <Code key={idx}>{c}</Code>)
                  ) : (
                    <span className="muted">{r.title || 'Chỉ cảnh báo'}</span>
                  )}
                </div>
              ),
            },
            ...(isAdmin
              ? [
                  {
                    key: 'ops',
                    label: 'Thao tác',
                    render: (r) =>
                      r.kind === 'PENDING' ? (
                        <div className="row-actions">
                          <button
                            className="apple-btn active"
                            onClick={() => approve(sid)}
                          >
                            Phê duyệt
                          </button>
                          <button
                            className="apple-btn secondary"
                            onClick={() => dismiss(sid)}
                          >
                            Bỏ qua
                          </button>
                        </div>
                      ) : (
                        <div className="row-actions">
                          <button
                            className="apple-btn tone-red"
                            onClick={() => rollback(sid, r.fault_class)}
                          >
                            Gỡ chặn
                          </button>
                        </div>
                      ),
                  },
                ]
              : []),
          ];

          const emptyMsg =
            alertRule.is_active === false
              ? 'Dịch vụ đang tạm tắt cảnh báo và ứng phó theo luật riêng (chỉ thu thập số liệu SLA).'
              : effMode === 'AUTO_PILOT'
              ? 'Không cần can thiệp. Chưa có đề xuất hay luật chặn nào đang áp dụng (chế độ Tự động ứng phó).'
              : 'Không cần can thiệp. Khi phát hiện sự cố, đề xuất lệnh ứng phó sẽ hiển thị tại bảng này để bạn phê duyệt.';

          return (
            <>
              <div className="card-title-row">
                <div className="card-title-left">
                  <h3>Xử lý tự động</h3>
                  {alertRule.is_active === false ? (
                    <span className="tg-pill off">Đã tắt cảnh báo</span>
                  ) : effMode === 'AUTO_PILOT' ? (
                    <span className="tg-pill on">Tự động ứng phó</span>
                  ) : (
                    <span className="tg-pill warn">Chờ phê duyệt</span>
                  )}
                </div>
                <div className="pending-actions">
                  {isAdmin && activeItems.length >= 2 && (
                    <button
                      className="apple-btn tone-red"
                      onClick={() => rollback(sid)}
                    >
                      Gỡ tất cả ({activeItems.length})
                    </button>
                  )}
                  <button className="apple-btn secondary" onClick={() => onNavigate('response')}>
                    Xem tất cả cảnh báo &amp; ứng phó →
                  </button>
                </div>
              </div>

              <DataTable
                columns={actionCols}
                rows={actionRows}
                pageSize={5}
                resetKey={sid}
                empty={emptyMsg}
              />
            </>
          );
        })()}
      </div>

      <PerformanceCharts history={state.performance_history || []} selectedService={sid} />

      <div className="apple-card">
        <div className="card-title-row">
          <h3>
            Cảnh báo của dịch vụ này ({filteredAlerts.length}/{serviceAlerts.length})
          </h3>
          {(alertQuery || alertSev !== 'ALL' || alertStatus !== 'ALL') && (
            <button
              type="button"
              className="apple-btn secondary preset-btn"
              onClick={() => {
                setAlertQuery('');
                setAlertSev('ALL');
                setAlertStatus('ALL');
              }}
            >
              Xóa bộ lọc
            </button>
          )}
        </div>

        <div className="table-toolbar">
          <input
            className="apple-select table-filter-input"
            placeholder="Tìm nguyên nhân, thời gian..."
            value={alertQuery}
            onChange={(e) => setAlertQuery(e.target.value)}
          />
          <select
            className="apple-select table-filter-select"
            value={alertSev}
            onChange={(e) => setAlertSev(e.target.value)}
          >
            <option value="ALL">Tất cả mức độ</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="warning">Warning</option>
            <option value="medium">Medium</option>
          </select>
          <select
            className="apple-select table-filter-select"
            value={alertStatus}
            onChange={(e) => setAlertStatus(e.target.value)}
          >
            <option value="ALL">Tất cả trạng thái</option>
            <option value="ACTIVE">ACTIVE</option>
            <option value="RESOLVED">RESOLVED</option>
          </select>
        </div>

        <DataTable
          columns={alertCols}
          rows={filteredAlerts}
          pageSize={5}
          resetKey={`${sid}|${alertQuery}|${alertSev}|${alertStatus}`}
          empty="Dịch vụ này chưa có cảnh báo nào khớp bộ lọc."
        />
      </div>
    </>
  );
}
