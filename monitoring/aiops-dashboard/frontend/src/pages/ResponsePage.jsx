import React, { useMemo, useState } from 'react';
import DataTable, { Badge, Code } from '../components/DataTable.jsx';
import { StatusPill, faultLabel, getRuleCommands } from '../lib.jsx';

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

function getServiceResponseMode(ev) {
  const ar = ev.alert_rule || {};
  const isCustom =
    ar.is_active === false ||
    Boolean(ar.response_mode && ar.response_mode !== 'INHERIT');
  const sourceLabel = isCustom ? 'Luật riêng' : 'Theo mặc định';
  if (ar.is_active === false) {
    return { key: 'DISABLED', label: 'Đã tắt cảnh báo', cls: 'tg-pill off', isCustom, sourceLabel };
  }
  const effMode =
    ar.effective_response_mode ||
    (ev.policy_gate?.auto_pilot ? 'AUTO_PILOT' : 'HUMAN_APPROVAL');
  if (effMode === 'AUTO_PILOT') {
    return { key: 'AUTO_PILOT', label: 'Tự động ứng phó', cls: 'tg-pill on', isCustom, sourceLabel };
  }
  return { key: 'HUMAN_APPROVAL', label: 'Chờ phê duyệt', cls: 'tg-pill warn', isCustom, sourceLabel };
}

export default function ResponsePage({ ctx, onOpenService }) {
  const { state, db, approve, dismiss, rollback, setPolicy } = ctx;
  const isAdmin = ctx.user?.role === 'ADMIN';
  const net = state.network_state || {};
  const evals = state.service_evaluations || {};
  const list = Object.values(evals);
  const activeRules = net.active_service_rules || [];
  const pending = net.pending_approvals || [];
  const auto = state.policy_gate?.auto_pilot;
  const tg = state.telegram || {};

  // Bộ lọc Bảng 1: Bảo vệ theo từng dịch vụ
  const [svcQuery, setSvcQuery] = useState('');
  const [svcFilter, setSvcFilter] = useState('ALL');
  const [svcModeFilter, setSvcModeFilter] = useState('ALL');

  // Bộ lọc Bảng 2: Cảnh báo
  const [alertQuery, setAlertQuery] = useState('');
  const [alertSvc, setAlertSvc] = useState('ALL');
  const [alertSev, setAlertSev] = useState('ALL');
  const [alertStatus, setAlertStatus] = useState('ALL');

  // Bộ lọc Bảng 3: Nhật ký ứng phó
  const [logQuery, setLogQuery] = useState('');
  const [logSvc, setLogSvc] = useState('ALL');
  const [logStatus, setLogStatus] = useState('ALL');

  const allAlerts = db?.Alerts || [];
  const allLogs = (db?.RemediationActions && db.RemediationActions.length > 0)
    ? db.RemediationActions
    : (state.audit_logs || []);

  const alertServiceOptions = useMemo(() => {
    const s = new Set(allAlerts.map((a) => a.service_id).filter(Boolean));
    return Array.from(s).sort();
  }, [allAlerts]);

  const logServiceOptions = useMemo(() => {
    const s = new Set(allLogs.map((r) => r.service_id).filter(Boolean));
    return Array.from(s).sort();
  }, [allLogs]);

  const logStatusOptions = useMemo(() => {
    const s = new Set(allLogs.map((r) => String(r.status || '').toUpperCase()).filter(Boolean));
    return Array.from(s).sort();
  }, [allLogs]);

  const filteredServices = useMemo(() => {
    const q = svcQuery.trim().toLowerCase();
    return list.filter((ev) => {
      const mode = getServiceResponseMode(ev);
      if (svcFilter === 'PROTECTED' && !ev.active_rule) return false;
      if (svcFilter === 'DEGRADED' && ev.severity_level === 'NORMAL') return false;
      if (svcFilter === 'NORMAL' && ev.severity_level !== 'NORMAL') return false;
      if (svcModeFilter !== 'ALL' && mode.key !== svcModeFilter) return false;
      if (!q) return true;
      return (
        String(ev.service_name || '').toLowerCase().includes(q) ||
        String(ev.service_id || '').toLowerCase().includes(q) ||
        String(ev.address || '').toLowerCase().includes(q) ||
        String(ev.port || '').includes(q) ||
        mode.label.toLowerCase().includes(q) ||
        mode.sourceLabel.toLowerCase().includes(q) ||
        String(ev.active_rule?.command || '').toLowerCase().includes(q)
      );
    });
  }, [list, svcQuery, svcFilter, svcModeFilter]);

  const filteredAlerts = useMemo(() => {
    const q = alertQuery.trim().toLowerCase();
    return allAlerts.filter((a) => {
      if (alertSvc !== 'ALL' && a.service_id !== alertSvc) return false;
      if (alertSev !== 'ALL' && String(a.severity || '').toLowerCase() !== alertSev) return false;
      if (alertStatus !== 'ALL' && String(a.status || '').toUpperCase() !== alertStatus) return false;
      if (!q) return true;
      return (
        String(a.service_id || '').toLowerCase().includes(q) ||
        String(a.alert_type || '').toLowerCase().includes(q) ||
        String(faultLabel(a.alert_type) || '').toLowerCase().includes(q) ||
        String(a.message || '').toLowerCase().includes(q) ||
        String(a.created_at || '').toLowerCase().includes(q)
      );
    });
  }, [allAlerts, alertQuery, alertSvc, alertSev, alertStatus]);

  const filteredLogs = useMemo(() => {
    const q = logQuery.trim().toLowerCase();
    return allLogs.filter((r) => {
      if (logSvc !== 'ALL' && r.service_id !== logSvc) return false;
      if (logStatus !== 'ALL' && String(r.status || '').toUpperCase() !== logStatus) return false;
      if (!q) return true;
      return (
        String(r.service_id || '').toLowerCase().includes(q) ||
        String(r.fault_class || '').toLowerCase().includes(q) ||
        String(faultLabel(r.fault_class) || '').toLowerCase().includes(q) ||
        String(r.action || '').toLowerCase().includes(q) ||
        String(r.actor || '').toLowerCase().includes(q) ||
        String(r.trigger || '').toLowerCase().includes(q) ||
        String(r.timestamp || r.time || '').toLowerCase().includes(q)
      );
    });
  }, [allLogs, logQuery, logSvc, logStatus]);

  const protectionCols = [
    {
      key: 'service_name',
      label: 'Dịch vụ',
      render: (ev) => (
        <>
          <strong>{ev.service_name}</strong>
          <div className="muted">
            <Code>{ev.address}:{ev.port}</Code>
          </div>
        </>
      ),
    },
    {
      key: 'severity_level',
      label: 'Tình trạng',
      render: (ev) => <StatusPill level={ev.severity_level} />,
    },
    {
      key: 'response_mode',
      label: 'Trạng thái ứng phó',
      render: (ev) => {
        const mode = getServiceResponseMode(ev);
        return (
          <div className="response-mode-cell">
            <span className={mode.cls}>{mode.label}</span>
            <span className={`response-mode-source ${mode.isCustom ? 'custom' : ''}`}>
              {mode.sourceLabel}
            </span>
          </div>
        );
      },
    },
    {
      key: 'active_rule',
      label: 'Số luật chặn',
      render: (ev) => {
        const cnt = getRuleCommands(ev.active_rule).length;
        const hasPending = Boolean(ev.pending_rule);
        if (cnt === 0 && !hasPending) {
          return <span className="muted">Không có luật chặn</span>;
        }
        return (
          <div className="rule-count-group">
            {cnt > 0 && <span className="rule-count-pill">{cnt} luật chặn</span>}
            {hasPending && <span className="tg-pill warn">1 chờ duyệt</span>}
          </div>
        );
      },
    },
    {
      key: 'actions',
      label: 'Thao tác',
      render: (ev) => (
        <div className="row-actions">
          <button
            type="button"
            className="apple-btn active"
            onClick={() => onOpenService?.(ev.service_id)}
          >
            Xem chi tiết
          </button>
          {isAdmin && ev.active_rule && (
            <button
              type="button"
              className="apple-btn tone-red"
              onClick={() => rollback(ev.service_id)}
            >
              Gỡ chặn
            </button>
          )}
        </div>
      ),
    },
  ];

  const alertCols = [
    { key: 'created_at', label: 'Thời gian', render: (r) => <Code>{r.created_at}</Code> },
    { key: 'service_id', label: 'Dịch vụ / Thiết bị', render: (r) => <Code>{r.service_id}</Code> },
    { key: 'alert_type', label: 'Nguyên nhân', render: (r) => <strong>{faultLabel(r.alert_type)}</strong> },
    { key: 'severity', label: 'Mức độ', render: (r) => <Badge value={r.severity} /> },
    { key: 'status', label: 'Trạng thái', render: (r) => <Badge value={r.status} /> },
  ];

  const hasSvcFilters = Boolean(svcQuery || svcFilter !== 'ALL' || svcModeFilter !== 'ALL');
  const hasAlertFilters = Boolean(alertQuery || alertSvc !== 'ALL' || alertSev !== 'ALL' || alertStatus !== 'ALL');
  const hasLogFilters = Boolean(logQuery || logSvc !== 'ALL' || logStatus !== 'ALL');
  const totalActiveCmds = activeRules.reduce((acc, r) => acc + Math.max(1, getRuleCommands(r).length), 0);

  return (
    <>
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Chế độ xử lý</h3>
          <div className="pending-actions">
            <span className={`tg-pill ${tg.enabled ? 'on' : 'off'}`}>
              {tg.enabled
                ? `Telegram: ${tg.bot_username || 'Đã kết nối'}`
                : 'Telegram: Chưa bật'}
            </span>
            {!isAdmin && <span className="role-pill role-viewer">Chế độ chỉ đọc</span>}
          </div>
        </div>
        <div className="mode-row">
          <button
            className={`apple-btn ${auto ? 'active' : ''}`}
            onClick={() => setPolicy({ auto_pilot: true })}
            disabled={!isAdmin}
          >
            Tự động ứng phó
          </button>

          <button
            className={`apple-btn ${!auto ? 'active' : ''}`}
            onClick={() => setPolicy({ auto_pilot: false })}
            disabled={!isAdmin}
          >
            Chờ phê duyệt
          </button>

          <span className="muted">
            {auto
              ? 'Hệ thống tự chặn ngay khi xác định được nguyên nhân.'
              : 'Hệ thống chỉ đề xuất, quản trị viên bấm phê duyệt mới thực thi (leo thang L1 → L2 → L3).'}
          </span>
        </div>
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
          <div className="card-title-left">
            <h3>Bảo vệ theo từng dịch vụ ({filteredServices.length}/{list.length})</h3>
            <span className="apple-tag">
              {totalActiveCmds > activeRules.length
                ? `${activeRules.length} dịch vụ · ${totalActiveCmds} luật chặn`
                : `${activeRules.length} dịch vụ đang có luật chặn`}
            </span>
          </div>
          {isAdmin && (
            <button
              type="button"
              className="apple-btn active"
              onClick={() => rollback()}
            >
              Gỡ chặn tất cả dịch vụ
            </button>
          )}
        </div>

        <div className="table-toolbar">
          <input
            className="apple-select table-filter-input"
            placeholder="Tìm dịch vụ, IP, cổng hoặc ứng phó..."
            value={svcQuery}
            onChange={(e) => setSvcQuery(e.target.value)}
          />
          <select
            className="apple-select table-filter-select"
            value={svcFilter}
            onChange={(e) => setSvcFilter(e.target.value)}
          >
            <option value="ALL">Tất cả tình trạng</option>
            <option value="PROTECTED">Đang có luật chặn</option>
            <option value="DEGRADED">Đang bất thường</option>
            <option value="NORMAL">Bình thường</option>
          </select>
          <select
            className="apple-select table-filter-select"
            value={svcModeFilter}
            onChange={(e) => setSvcModeFilter(e.target.value)}
          >
            <option value="ALL">Tất cả ứng phó</option>
            <option value="AUTO_PILOT">Tự động ứng phó</option>
            <option value="HUMAN_APPROVAL">Chờ phê duyệt</option>
            <option value="DISABLED">Đã tắt cảnh báo</option>
          </select>
          {hasSvcFilters && (
            <button
              type="button"
              className="apple-btn secondary preset-btn"
              onClick={() => {
                setSvcQuery('');
                setSvcFilter('ALL');
                setSvcModeFilter('ALL');
              }}
            >
              Xóa bộ lọc
            </button>
          )}
        </div>

        <DataTable
          columns={protectionCols}
          rows={filteredServices}
          pageSize={8}
          resetKey={`${svcQuery}|${svcFilter}|${svcModeFilter}`}
          empty="Không có dịch vụ nào khớp bộ lọc."
        />
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>
            Cảnh báo AI &amp; Hạ tầng ({filteredAlerts.length}/{allAlerts.length})
          </h3>
          {hasAlertFilters && (
            <button
              type="button"
              className="apple-btn secondary preset-btn"
              onClick={() => {
                setAlertQuery('');
                setAlertSvc('ALL');
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
            placeholder="Tìm nguyên nhân, dịch vụ, nội dung..."
            value={alertQuery}
            onChange={(e) => setAlertQuery(e.target.value)}
          />
          <select
            className="apple-select table-filter-select"
            value={alertSvc}
            onChange={(e) => setAlertSvc(e.target.value)}
          >
            <option value="ALL">Tất cả dịch vụ</option>
            {alertServiceOptions.map((sid) => (
              <option key={sid} value={sid}>
                {sid}
              </option>
            ))}
          </select>
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
          pageSize={10}
          resetKey={`${alertQuery}|${alertSvc}|${alertSev}|${alertStatus}`}
          empty="Không có cảnh báo nào khớp bộ lọc."
        />
      </div>

      <div className="apple-card">
        <div className="card-title-row">
          <h3>
            Nhật ký ứng phó ({filteredLogs.length}/{allLogs.length})
          </h3>
          {hasLogFilters && (
            <button
              type="button"
              className="apple-btn secondary preset-btn"
              onClick={() => {
                setLogQuery('');
                setLogSvc('ALL');
                setLogStatus('ALL');
              }}
            >
              Xóa bộ lọc
            </button>
          )}
        </div>

        <div className="table-toolbar">
          <input
            className="apple-select table-filter-input"
            placeholder="Tìm hành động, IP, nguyên nhân, người thực hiện..."
            value={logQuery}
            onChange={(e) => setLogQuery(e.target.value)}
          />
          <select
            className="apple-select table-filter-select"
            value={logSvc}
            onChange={(e) => setLogSvc(e.target.value)}
          >
            <option value="ALL">Tất cả dịch vụ</option>
            {logServiceOptions.map((sid) => (
              <option key={sid} value={sid}>
                {sid}
              </option>
            ))}
          </select>
          <select
            className="apple-select table-filter-select"
            value={logStatus}
            onChange={(e) => setLogStatus(e.target.value)}
          >
            <option value="ALL">Tất cả kết quả</option>
            {logStatusOptions.map((st) => (
              <option key={st} value={st}>
                {st}
              </option>
            ))}
          </select>
        </div>

        <DataTable
          columns={auditColumns}
          rows={filteredLogs}
          pageSize={10}
          resetKey={`${logQuery}|${logSvc}|${logStatus}`}
          empty="Không có nhật ký nào khớp bộ lọc."
        />
      </div>
    </>
  );
}
