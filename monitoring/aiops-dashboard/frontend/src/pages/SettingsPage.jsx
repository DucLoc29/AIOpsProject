import React, { useEffect, useState } from 'react';

const PRESETS = [
  { label: '15s / 30s', l2: 15, l3: 30 },
  { label: '30s / 60s', l2: 30, l3: 60 },
  { label: '120s / 300s', l2: 120, l3: 300 },
];

const POLL_OPTIONS = [
  { label: '1 giây', value: 1000 },
  { label: '2 giây (Mặc định)', value: 2000 },
  { label: '3 giây', value: 3000 },
  { label: '5 giây', value: 5000 },
];

const RESPONSE_MODE_OPTIONS = [
  { value: 'INHERIT', label: 'Theo mặc định chung' },
  { value: 'AUTO_PILOT', label: 'Tự động ứng phó' },
  { value: 'HUMAN_APPROVAL', label: 'Chờ phê duyệt' },
];

const GLOBAL_SCOPE = '__GLOBAL__';

function CompactToggle({ label, checked, onChange }) {
  return (
    <div className="compact-toggle">
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        className={`switch ${checked ? 'on' : ''}`}
        onClick={() => onChange(!checked)}
      >
        <span className="knob" />
      </button>
    </div>
  );
}

function validateAiBlock(vals, prefix) {
  const m1Med = Number(vals.m1_threshold_medium);
  const m1High = Number(vals.m1_threshold_high);
  const m1Crit = Number(vals.m1_threshold_critical);
  if (!Number.isFinite(m1Med) || !Number.isFinite(m1High) || !Number.isFinite(m1Crit)) {
    return `${prefix}: các ngưỡng Model 1 phải là số thập phân hợp lệ.`;
  }
  if (m1High < m1Med + 0.01) {
    return `${prefix}: Model 1 Ngưỡng HIGH (${m1High}) phải lớn hơn MEDIUM (${m1Med}) ít nhất 0.01.`;
  }
  if (m1Crit < m1High + 0.01) {
    return `${prefix}: Model 1 Ngưỡng CRITICAL (${m1Crit}) phải lớn hơn HIGH (${m1High}) ít nhất 0.01.`;
  }
  const fcThr = Number(vals.forecast_threshold_pct);
  const fcCrit = Number(vals.forecast_critical_pct);
  if (fcCrit < fcThr + 5) {
    return `${prefix}: Dự báo 5m Ngưỡng CRITICAL (${fcCrit}%) phải lớn hơn ngưỡng kích hoạt (${fcThr}%) ít nhất 5%.`;
  }
  return '';
}

export default function SettingsPage({ ctx }) {
  const [form, setForm] = useState(null);
  const [savedSnapshot, setSavedSnapshot] = useState(null);
  const [serviceRules, setServiceRules] = useState({});
  const [savedRulesSnapshot, setSavedRulesSnapshot] = useState('{}');
  const [servicesList, setServicesList] = useState([]);
  const [aiScope, setAiScope] = useState(GLOBAL_SCOPE);
  const [tgInfo, setTgInfo] = useState({});
  const [modelInfo, setModelInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testingTg, setTestingTg] = useState(false);
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');

  useEffect(() => {
    ctx
      .getSettings()
      .then((res) => {
        setForm(res.settings);
        setSavedSnapshot(JSON.stringify(res.settings));
        const sr = res.service_rules || {};
        setServiceRules(sr);
        setSavedRulesSnapshot(JSON.stringify(sr));
        setServicesList(res.services || []);
        setTgInfo(res.telegram || {});
        setModelInfo(res.model_info || null);
      })
      .catch((ex) => {
        if (ex.status === 401) ctx.forceLogout();
        else setErr(ex.message || 'Không thể tải cài đặt');
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading || !form) {
    return (
      <div className="apple-card">
        {err ? <div className="error-banner">{err}</div> : <p className="muted">Đang tải cài đặt...</p>}
      </div>
    );
  }

  const isDirty =
    savedSnapshot !== JSON.stringify(form) ||
    savedRulesSnapshot !== JSON.stringify(serviceRules);

  const setField = (k, v) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setOkMsg('');
    setErr('');
  };
  const setFields = (patch) => {
    setForm((prev) => ({ ...prev, ...patch }));
    setOkMsg('');
    setErr('');
  };

  const isGlobalScope = aiScope === GLOBAL_SCOPE;
  const activeCustomRule = !isGlobalScope ? serviceRules[aiScope] : null;
  const customRuleCount = Object.keys(serviceRules).length;

  // Giá trị hiển thị cho khối AI: nếu đang chọn dịch vụ có AlertRule riêng -> dùng rule riêng, ngược lại kế thừa Global
  const aiView = isGlobalScope
    ? form
    : activeCustomRule || {
        is_active: true,
        response_mode: 'INHERIT',
        m1_threshold_medium: form.m1_threshold_medium,
        m1_threshold_high: form.m1_threshold_high,
        m1_threshold_critical: form.m1_threshold_critical,
        forecast_threshold_pct: form.forecast_threshold_pct,
        forecast_critical_pct: form.forecast_critical_pct,
        m2_confidence_threshold_pct: form.m2_confidence_threshold_pct,
      };

  const updateAiScopeFields = (patch) => {
    setOkMsg('');
    setErr('');
    if (isGlobalScope) {
      setForm((prev) => ({ ...prev, ...patch }));
      return;
    }
    setServiceRules((prev) => {
      const base = prev[aiScope] || {
        service_id: aiScope,
        is_active: true,
        response_mode: 'INHERIT',
        m1_threshold_medium: Number(form.m1_threshold_medium),
        m1_threshold_high: Number(form.m1_threshold_high),
        m1_threshold_critical: Number(form.m1_threshold_critical),
        forecast_threshold_pct: Number(form.forecast_threshold_pct),
        forecast_critical_pct: Number(form.forecast_critical_pct),
        m2_confidence_threshold_pct: Number(form.m2_confidence_threshold_pct),
      };
      return { ...prev, [aiScope]: { ...base, ...patch } };
    });
  };

  const removeCurrentServiceRule = () => {
    if (isGlobalScope) return;
    setServiceRules((prev) => {
      const next = { ...prev };
      delete next[aiScope];
      return next;
    });
    setOkMsg('');
    setErr('');
  };

  const calDefaults = modelInfo?.calibrated_defaults || {
    m1_threshold_medium: 0.5451,
    m1_threshold_high: 0.6061,
    m1_threshold_critical: 0.6709,
    forecast_threshold_pct: 60.0,
    forecast_critical_pct: 80.0,
    m2_confidence_threshold_pct: 70.0,
  };

  const AI_PRESETS = [
    {
      label: 'Chuẩn gốc',
      values: {
        m1_threshold_medium: calDefaults.m1_threshold_medium,
        m1_threshold_high: calDefaults.m1_threshold_high,
        m1_threshold_critical: calDefaults.m1_threshold_critical,
        forecast_threshold_pct: calDefaults.forecast_threshold_pct,
        forecast_critical_pct: calDefaults.forecast_critical_pct,
        m2_confidence_threshold_pct: calDefaults.m2_confidence_threshold_pct,
      },
    },
    {
      label: 'Nhạy cao',
      values: {
        m1_threshold_medium: 0.51,
        m1_threshold_high: 0.57,
        m1_threshold_critical: 0.64,
        forecast_threshold_pct: 55,
        forecast_critical_pct: 75,
        m2_confidence_threshold_pct: 65,
      },
    },
    {
      label: 'Giảm báo giả',
      values: {
        m1_threshold_medium: 0.57,
        m1_threshold_high: 0.63,
        m1_threshold_critical: 0.70,
        forecast_threshold_pct: 68,
        forecast_critical_pct: 85,
        m2_confidence_threshold_pct: 78,
      },
    },
  ];

  const isPresetMatch = (vals) =>
    Math.abs(Number(aiView.m1_threshold_medium) - vals.m1_threshold_medium) < 0.0002 &&
    Math.abs(Number(aiView.m1_threshold_high) - vals.m1_threshold_high) < 0.0002 &&
    Math.abs(Number(aiView.m1_threshold_critical) - vals.m1_threshold_critical) < 0.0002 &&
    Math.abs(Number(aiView.forecast_threshold_pct) - vals.forecast_threshold_pct) < 0.1 &&
    Math.abs(Number(aiView.forecast_critical_pct) - vals.forecast_critical_pct) < 0.1 &&
    Math.abs(Number(aiView.m2_confidence_threshold_pct) - vals.m2_confidence_threshold_pct) < 0.1;

  const handleSave = async (e) => {
    e.preventDefault();
    setErr('');
    setOkMsg('');

    const l2 = Number(form.escalation_l2_seconds);
    const l3 = Number(form.escalation_l3_seconds);
    if (l3 < l2 + 5) {
      setErr(`Thời gian Tầng 3 (${l3}s) phải lớn hơn Tầng 2 ít nhất 5 giây.`);
      return;
    }

    const globalErr = validateAiBlock(form, 'Mặc định chung');
    if (globalErr) {
      setErr(globalErr);
      return;
    }

    // Kiểm tra và đóng gói toàn bộ AlertRule theo dịch vụ (null = gỡ luật riêng, kế thừa mặc định chung)
    const normalizedServiceRules = {};
    for (const s of servicesList) {
      const sid = s.service_id;
      const r = serviceRules[sid];
      if (!r) {
        normalizedServiceRules[sid] = null;
        continue;
      }
      const rErr = validateAiBlock(r, s.service_name || sid);
      if (rErr) {
        setErr(rErr);
        return;
      }
      normalizedServiceRules[sid] = {
        is_active: Boolean(r.is_active !== false),
        response_mode: r.response_mode || 'INHERIT',
        m1_threshold_medium: Number(r.m1_threshold_medium),
        m1_threshold_high: Number(r.m1_threshold_high),
        m1_threshold_critical: Number(r.m1_threshold_critical),
        forecast_threshold_pct: Number(r.forecast_threshold_pct),
        forecast_critical_pct: Number(r.forecast_critical_pct),
        m2_confidence_threshold_pct: Number(r.m2_confidence_threshold_pct),
      };
    }

    setSaving(true);
    try {
      const payload = {
        ...form,
        m1_threshold_medium: Number(form.m1_threshold_medium),
        m1_threshold_high: Number(form.m1_threshold_high),
        m1_threshold_critical: Number(form.m1_threshold_critical),
        service_rules: normalizedServiceRules,
      };
      const res = await ctx.updateSettings(payload);
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      const nextRules = res.service_rules || {};
      setServiceRules(nextRules);
      setSavedRulesSnapshot(JSON.stringify(nextRules));
      if (res.services) setServicesList(res.services);
      setTgInfo(res.telegram || tgInfo);
      if (res.model_info) setModelInfo(res.model_info);
      setOkMsg('Đã lưu & áp dụng cài đặt.');
    } catch (ex) {
      if (ex.status === 401) ctx.forceLogout();
      else setErr(ex.message || 'Lưu cài đặt thất bại');
    } finally {
      setSaving(false);
    }
  };

  const handleLoadDefaults = () => {
    setErr('');
    setForm((prev) => ({
      ...prev,
      auto_pilot: true,
      m1_threshold_medium: calDefaults.m1_threshold_medium,
      m1_threshold_high: calDefaults.m1_threshold_high,
      m1_threshold_critical: calDefaults.m1_threshold_critical,
      forecast_threshold_pct: calDefaults.forecast_threshold_pct,
      forecast_critical_pct: calDefaults.forecast_critical_pct,
      m2_confidence_threshold_pct: calDefaults.m2_confidence_threshold_pct,
      escalation_l2_seconds: 30,
      escalation_l3_seconds: 60,
      l3_auto_mitigate: true,
      l3_min_confidence_pct: 80.0,
      auto_resolved_cycles: 2,
      incident_cooldown_seconds: 12,
      dismissed_cooldown_seconds: 45,
      spike_log_cooldown_seconds: 18,
      telegram_notify_ai: true,
      telegram_notify_infra: true,
      telegram_notify_resolved: true,
      ui_poll_interval_ms: 2000,
    }));
    setServiceRules({});
    setAiScope(GLOBAL_SCOPE);
    setOkMsg('Đã nạp cấu hình mặc định vào biểu mẫu — bấm "Lưu cài đặt" để áp dụng.');
  };

  const handleDiscardChanges = () => {
    try {
      if (savedSnapshot) setForm(JSON.parse(savedSnapshot));
      if (savedRulesSnapshot) setServiceRules(JSON.parse(savedRulesSnapshot));
    } catch (_) {
      // ignore
    }
    setErr('');
    setOkMsg('');
  };

  const handleTestTelegram = async () => {
    setTestingTg(true);
    setErr('');
    setOkMsg('');
    try {
      const res = await ctx.testTelegram();
      if (res.telegram) setTgInfo(res.telegram);
      setOkMsg(res.message || 'Đã gửi tin thử Telegram.');
    } catch (ex) {
      setErr(ex.message || 'Gửi tin thử thất bại.');
    } finally {
      setTestingTg(false);
    }
  };

  const l2 = Number(form.escalation_l2_seconds) || 30;
  const l3 = Number(form.escalation_l3_seconds) || 60;

  return (
    <form className="settings-form" onSubmit={handleSave}>
      <div className="settings-top-actions">
        <span className={isDirty ? 'tier-pill tier-L2' : 'tg-pill on'}>
          {isDirty ? 'Có thay đổi chưa lưu — bấm "Lưu cài đặt" để áp dụng' : 'Đang đồng bộ'}
        </span>
        <div className="pending-actions">
          {isDirty && (
            <button
              type="button"
              className="apple-btn secondary"
              onClick={handleDiscardChanges}
              disabled={saving}
            >
              Hủy thay đổi
            </button>
          )}
          <button
            type="button"
            className="apple-btn secondary"
            onClick={handleLoadDefaults}
            disabled={saving}
          >
            Nạp mặc định
          </button>
          <button type="submit" className="apple-btn active" disabled={saving || !isDirty}>
            {saving ? 'Đang lưu...' : 'Lưu cài đặt'}
          </button>
        </div>
      </div>

      {err && <div className="error-banner">{err}</div>}
      {okMsg && <div className="success-banner">{okMsg}</div>}

      {/* Khối 1: Leo thang cảnh báo & Vận hành */}
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Leo thang cảnh báo &amp; Vận hành</h3>
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className={`apple-btn secondary preset-btn ${l2 === p.l2 && l3 === p.l3 ? 'active' : ''}`}
                onClick={() =>
                  setFields({
                    escalation_l2_seconds: p.l2,
                    escalation_l3_seconds: p.l3,
                  })
                }
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-grid-3">
          <label className="policy-field">
            <span>Leo thang L1 → L2 (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={600}
              value={form.escalation_l2_seconds}
              onChange={(e) => setField('escalation_l2_seconds', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Leo thang L2 → L3 (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={10}
              max={1800}
              value={form.escalation_l3_seconds}
              onChange={(e) => setField('escalation_l3_seconds', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Chu kỳ hồi phục Auto-Resolved</span>
            <input
              type="number"
              className="apple-select"
              min={1}
              max={10}
              value={form.auto_resolved_cycles}
              onChange={(e) => setField('auto_resolved_cycles', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Độ tin cậy L3 tối thiểu (%)</span>
            <input
              type="number"
              className="apple-select"
              min={70}
              max={99}
              disabled={!form.l3_auto_mitigate}
              value={form.l3_min_confidence_pct}
              onChange={(e) => setField('l3_min_confidence_pct', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Chống lặp sự cố (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={120}
              value={form.incident_cooldown_seconds}
              onChange={(e) => setField('incident_cooldown_seconds', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Tạm ẩn sau "Bỏ qua" (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={10}
              max={600}
              value={form.dismissed_cooldown_seconds}
              onChange={(e) => setField('dismissed_cooldown_seconds', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Chống lặp TransientSpike (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={120}
              value={form.spike_log_cooldown_seconds}
              onChange={(e) => setField('spike_log_cooldown_seconds', Number(e.target.value))}
            />
          </label>

          <label className="policy-field">
            <span>Tần suất cập nhật Web</span>
            <select
              className="apple-select"
              value={Number(form.ui_poll_interval_ms) || 2000}
              onChange={(e) => setField('ui_poll_interval_ms', Number(e.target.value))}
            >
              {POLL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="compact-toggle-grid" style={{ gridTemplateColumns: '1fr' }}>
          <CompactToggle
            label="Tự bảo vệ ở Tầng 3 khi quá hạn không phản hồi"
            checked={Boolean(form.l3_auto_mitigate)}
            onChange={(v) => setField('l3_auto_mitigate', v)}
          />
        </div>
      </div>

      {/* Khối 2: Mô hình AI & Luật cảnh báo */}
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Mô hình AI &amp; Luật cảnh báo</h3>
          <div className="preset-row">
            {AI_PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className={`apple-btn secondary preset-btn ${isPresetMatch(p.values) ? 'active' : ''}`}
                onClick={() => updateAiScopeFields(p.values)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        <div className="settings-scope-bar">
          <label className="settings-scope-select">
            <span>Phạm vi áp dụng</span>
            <select
              className="apple-select"
              value={aiScope}
              onChange={(e) => setAiScope(e.target.value)}
            >
              <option value={GLOBAL_SCOPE}>
                Mặc định chung (Tất cả dịch vụ)
              </option>
              {servicesList.map((s) => (
                <option key={s.service_id} value={s.service_id}>
                  {s.service_name} (:{s.port}){serviceRules[s.service_id] ? ' — [Luật riêng]' : ''}
                </option>
              ))}
            </select>
          </label>

          <div className="settings-scope-meta">
            {isGlobalScope ? (
              <span className="apple-tag">
                {customRuleCount > 0
                  ? `${customRuleCount} dịch vụ đang dùng luật riêng`
                  : 'Tất cả dịch vụ dùng chung ngưỡng này'}
              </span>
            ) : activeCustomRule ? (
              <>
                <span className="tier-pill tier-L1">AlertRule riêng</span>
                <button
                  type="button"
                  className="apple-btn secondary preset-btn"
                  onClick={removeCurrentServiceRule}
                >
                  Về mặc định chung
                </button>
              </>
            ) : (
              <span className="apple-tag">Đang kế thừa mặc định chung</span>
            )}
          </div>
        </div>

        <div className="settings-grid-3">
          <label className="policy-field">
            <span>Model 1 EIF · Ngưỡng MEDIUM</span>
            <input
              type="number"
              step="0.0001"
              className="apple-select"
              min={0.35}
              max={0.85}
              value={aiView.m1_threshold_medium}
              onChange={(e) => updateAiScopeFields({ m1_threshold_medium: e.target.value })}
            />
          </label>

          <label className="policy-field">
            <span>Model 1 EIF · Ngưỡng HIGH</span>
            <input
              type="number"
              step="0.0001"
              className="apple-select"
              min={0.40}
              max={0.88}
              value={aiView.m1_threshold_high}
              onChange={(e) => updateAiScopeFields({ m1_threshold_high: e.target.value })}
            />
          </label>

          <label className="policy-field">
            <span>Model 1 EIF · Ngưỡng CRITICAL</span>
            <input
              type="number"
              step="0.0001"
              className="apple-select"
              min={0.45}
              max={0.95}
              value={aiView.m1_threshold_critical}
              onChange={(e) => updateAiScopeFields({ m1_threshold_critical: e.target.value })}
            />
          </label>

          <label className="policy-field">
            <span>Dự báo 5m · Kích hoạt Model 2 (%)</span>
            <input
              type="number"
              step="1"
              className="apple-select"
              min={40}
              max={90}
              value={aiView.forecast_threshold_pct}
              onChange={(e) => updateAiScopeFields({ forecast_threshold_pct: Number(e.target.value) })}
            />
          </label>

          <label className="policy-field">
            <span>Dự báo 5m · Ngưỡng CRITICAL (%)</span>
            <input
              type="number"
              step="1"
              className="apple-select"
              min={55}
              max={98}
              value={aiView.forecast_critical_pct}
              onChange={(e) => updateAiScopeFields({ forecast_critical_pct: Number(e.target.value) })}
            />
          </label>

          <label className="policy-field">
            <span>Model 2 RCA · Tin cậy tối thiểu (%)</span>
            <input
              type="number"
              step="1"
              className="apple-select"
              min={50}
              max={95}
              value={aiView.m2_confidence_threshold_pct}
              onChange={(e) => updateAiScopeFields({ m2_confidence_threshold_pct: Number(e.target.value) })}
            />
          </label>
        </div>

        {isGlobalScope ? (
          <div className="compact-toggle-grid" style={{ gridTemplateColumns: '1fr' }}>
            <CompactToggle
              label="Tự động ứng phó mặc định"
              checked={Boolean(form.auto_pilot)}
              onChange={(v) => setField('auto_pilot', v)}
            />
          </div>
        ) : (
          <div className="settings-inline-row">
            <label className="policy-field">
              <span>Chế độ ứng phó của dịch vụ</span>
              <select
                className="apple-select"
                value={aiView.response_mode || 'INHERIT'}
                onChange={(e) => updateAiScopeFields({ response_mode: e.target.value })}
              >
                {RESPONSE_MODE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <CompactToggle
              label="Bật giám sát cảnh báo cho dịch vụ này"
              checked={aiView.is_active !== false}
              onChange={(v) => updateAiScopeFields({ is_active: v })}
            />
          </div>
        )}

        <div className="settings-model-line">
          Ngưỡng hiệu chuẩn gốc — Model 1:{' '}
          <code>MED {calDefaults.m1_threshold_medium}</code> ·{' '}
          <code>HIGH {calDefaults.m1_threshold_high}</code> ·{' '}
          <code>CRIT {calDefaults.m1_threshold_critical}</code> · Dự báo 5m:{' '}
          <code>{calDefaults.forecast_threshold_pct}% / {calDefaults.forecast_critical_pct}%</code> · Model 2:{' '}
          <code>≥ {calDefaults.m2_confidence_threshold_pct}%</code>
        </div>
      </div>

      {/* Khối 3: Telegram ChatOps & Alertmanager */}
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Telegram ChatOps</h3>
          <div className="pending-actions">
            <span className={`tg-pill ${tgInfo.enabled ? 'on' : 'off'}`}>
              {tgInfo.enabled
                ? tgInfo.bot_username || 'Đã kết nối'
                : 'Chưa cấu hình (.env)'}
            </span>
            {tgInfo.enabled && (
              <button
                type="button"
                className="apple-btn active"
                onClick={handleTestTelegram}
                disabled={testingTg}
              >
                {testingTg ? 'Đang gửi...' : 'Gửi tin thử'}
              </button>
            )}
          </div>
        </div>

        <div className="compact-toggle-grid three">
          <CompactToggle
            label="Cảnh báo sự cố AI"
            checked={Boolean(form.telegram_notify_ai)}
            onChange={(v) => setField('telegram_notify_ai', v)}
          />
          <CompactToggle
            label="Hạ tầng Alertmanager"
            checked={Boolean(form.telegram_notify_infra)}
            onChange={(v) => setField('telegram_notify_infra', v)}
          />
          <CompactToggle
            label="Thông báo hồi phục"
            checked={Boolean(form.telegram_notify_resolved)}
            onChange={(v) => setField('telegram_notify_resolved', v)}
          />
        </div>

        <div className="settings-grid-3" style={{ marginTop: 14 }}>
          <label className="policy-field">
            <span>Chat ID chính (L1)</span>
            <input
              type="text"
              className="apple-select"
              placeholder="VD: 1234567890"
              value={form.telegram_chat_id || ''}
              onChange={(e) => setField('telegram_chat_id', e.target.value)}
            />
          </label>

          <label className="policy-field">
            <span>Chat ID Tầng 2/3 (tùy chọn)</span>
            <input
              type="text"
              className="apple-select"
              placeholder="Để trống = dùng Chat ID chính"
              value={form.telegram_l2_chat_id || ''}
              onChange={(e) => setField('telegram_l2_chat_id', e.target.value)}
            />
          </label>

          <label className="policy-field">
            <span>Telegram Admin IDs</span>
            <input
              type="text"
              className="apple-select"
              placeholder="Cách nhau bằng dấu phẩy"
              value={form.telegram_admin_ids || ''}
              onChange={(e) => setField('telegram_admin_ids', e.target.value)}
            />
          </label>
        </div>
      </div>
    </form>
  );
}
