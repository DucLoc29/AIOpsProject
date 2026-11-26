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

export default function SettingsPage({ ctx }) {
  const [form, setForm] = useState(null);
  const [savedSnapshot, setSavedSnapshot] = useState(null);
  const [tgInfo, setTgInfo] = useState({});
  const [modelInfo, setModelInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [testingTg, setTestingTg] = useState(false);
  const [err, setErr] = useState('');
  const [okMsg, setOkMsg] = useState('');

  useEffect(() => {
    ctx
      .getSettings()
      .then((res) => {
        setForm(res.settings);
        setSavedSnapshot(JSON.stringify(res.settings));
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

  const isDirty = savedSnapshot !== JSON.stringify(form);
  const setField = (k, v) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setOkMsg('');
    setErr('');
  };

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

    setSaving(true);
    try {
      const res = await ctx.updateSettings(form);
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      setTgInfo(res.telegram || tgInfo);
      setOkMsg('Đã lưu & áp dụng cài đặt.');
    } catch (ex) {
      if (ex.status === 401) ctx.forceLogout();
      else setErr(ex.message || 'Lưu cài đặt thất bại');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!window.confirm('Khôi phục toàn bộ thông số về mặc định?')) return;
    setResetting(true);
    setErr('');
    setOkMsg('');
    try {
      const res = await ctx.resetSettings();
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      setTgInfo(res.telegram || tgInfo);
      setOkMsg('Đã khôi phục mặc định.');
    } catch (ex) {
      setErr(ex.message || 'Khôi phục thất bại');
    } finally {
      setResetting(false);
    }
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
          {isDirty ? 'Có thay đổi chưa lưu' : 'Đang đồng bộ'}
        </span>
        <div className="pending-actions">
          <button
            type="button"
            className="apple-btn secondary"
            onClick={handleReset}
            disabled={resetting || saving}
          >
            {resetting ? 'Đang đặt lại...' : 'Mặc định'}
          </button>
          <button type="submit" className="apple-btn active" disabled={saving || !isDirty}>
            {saving ? 'Đang lưu...' : 'Lưu cài đặt'}
          </button>
        </div>
      </div>

      {err && <div className="error-banner">{err}</div>}
      {okMsg && <div className="success-banner">{okMsg}</div>}

      {/* Khối 1: Cảnh báo leo thang, AI Policy & Cooldown */}
      <div className="apple-card">
        <div className="card-title-row">
          <h3>Leo thang cảnh báo &amp; Ứng phó</h3>
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                className={`apple-btn secondary preset-btn ${l2 === p.l2 && l3 === p.l3 ? 'active' : ''}`}
                onClick={() => {
                  setField('escalation_l2_seconds', p.l2);
                  setField('escalation_l3_seconds', p.l3);
                }}
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
            <span>Ngưỡng dự báo 5m (%)</span>
            <input
              type="number"
              className="apple-select"
              min={40}
              max={90}
              value={form.forecast_threshold_pct}
              onChange={(e) => setField('forecast_threshold_pct', Number(e.target.value))}
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

        <div className="compact-toggle-grid">
          <CompactToggle
            label="Tự động ứng phó (Auto-Pilot)"
            checked={Boolean(form.auto_pilot)}
            onChange={(v) => setField('auto_pilot', v)}
          />
          <CompactToggle
            label="Tự bảo vệ ở Tầng 3 (trừ Unknown_Anomaly)"
            checked={Boolean(form.l3_auto_mitigate)}
            onChange={(v) => setField('l3_auto_mitigate', v)}
          />
        </div>
      </div>

      {/* Khối 2: Telegram ChatOps & Alertmanager */}
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
                className="apple-btn secondary"
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

        {modelInfo && (
          <div className="settings-model-line">
            Ngưỡng AI cố định — Model 1 EIF: <code>MED ≥ {modelInfo.model1_thresholds?.MEDIUM}</code> ·{' '}
            <code>HIGH ≥ {modelInfo.model1_thresholds?.HIGH}</code> ·{' '}
            <code>CRIT ≥ {modelInfo.model1_thresholds?.CRITICAL}</code> · Model 2 RCA:{' '}
            <code>≥ {modelInfo.model2_confidence_threshold_pct}%</code>
          </div>
        )}
      </div>
    </form>
  );
}
