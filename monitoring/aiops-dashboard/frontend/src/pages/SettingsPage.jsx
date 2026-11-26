import React, { useEffect, useState } from 'react';

const ESCALATION_PRESETS = [
  { label: 'Demo nhanh (15s / 30s)', l2: 15, l3: 30 },
  { label: 'Chuẩn đồ án (30s / 60s)', l2: 30, l3: 60 },
  { label: 'Vận hành NOC (120s / 300s)', l2: 120, l3: 300 },
];

const POLL_OPTIONS = [
  { label: '1 giây (Rất mượt)', value: 1000 },
  { label: '2 giây (Mặc định · Đồng bộ Telemetry)', value: 2000 },
  { label: '3 giây (Tiết kiệm băng thông)', value: 3000 },
  { label: '5 giây (Nhẹ máy)', value: 5000 },
];

function ToggleRow({ title, desc, checked, onChange }) {
  return (
    <div className="setting-toggle-row">
      <div>
        <div className="setting-label">{title}</div>
        <div className="setting-desc">{desc}</div>
      </div>
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

  const loadSettings = async () => {
    try {
      const res = await ctx.getSettings();
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      setTgInfo(res.telegram || {});
      setModelInfo(res.model_info || null);
      setErr('');
    } catch (ex) {
      if (ex.status === 401) {
        ctx.forceLogout();
        return;
      }
      setErr(ex.message || 'Không thể tải cấu hình hệ thống');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  if (loading || !form) {
    return (
      <div className="apple-card">
        {err ? <div className="error-banner">{err}</div> : <p className="muted">Đang tải cài đặt chung...</p>}
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
      setErr(`Thời gian leo thang Tầng 3 (${l3}s) phải lớn hơn Tầng 2 ít nhất 5 giây (≥ ${l2 + 5}s).`);
      return;
    }

    setSaving(true);
    try {
      const res = await ctx.updateSettings(form);
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      setTgInfo(res.telegram || tgInfo);
      setOkMsg('Đã lưu & áp dụng cài đặt ngay lập tức (đã đồng bộ MongoDB Atlas).');
    } catch (ex) {
      if (ex.status === 401) {
        ctx.forceLogout();
        return;
      }
      setErr(ex.message || 'Lưu cài đặt thất bại');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!window.confirm('Khôi phục toàn bộ thông số vận hành về mặc định chuẩn đồ án? (Giữ nguyên kết nối Telegram Chat ID)')) {
      return;
    }
    setResetting(true);
    setErr('');
    setOkMsg('');
    try {
      const res = await ctx.resetSettings();
      setForm(res.settings);
      setSavedSnapshot(JSON.stringify(res.settings));
      setTgInfo(res.telegram || tgInfo);
      setOkMsg('Đã khôi phục toàn bộ thông số về mặc định chuẩn.');
    } catch (ex) {
      setErr(ex.message || 'Khôi phục mặc định thất bại');
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
      setOkMsg(res.message || 'Đã gửi tin nhắn kiểm thử tới Telegram.');
    } catch (ex) {
      setErr(ex.message || 'Gửi tin nhắn thử Telegram thất bại.');
    } finally {
      setTestingTg(false);
    }
  };

  const l2 = Number(form.escalation_l2_seconds) || 30;
  const l3 = Number(form.escalation_l3_seconds) || 60;
  const cycles = Number(form.auto_resolved_cycles) || 2;

  return (
    <form className="settings-form" onSubmit={handleSave}>
      {/* Thanh thao tác trên cùng */}
      <div className="apple-card settings-sticky-bar">
        <div className="settings-bar-left">
          <h3>Trung tâm Cấu hình Hệ thống</h3>
          {isDirty ? (
            <span className="tier-pill tier-L2">Có thay đổi chưa lưu</span>
          ) : (
            <span className="tg-pill on">Đã đồng bộ thời gian thực</span>
          )}
        </div>
        <div className="pending-actions">
          <button
            type="button"
            className="apple-btn secondary"
            onClick={handleReset}
            disabled={resetting || saving}
          >
            {resetting ? 'Đang khôi phục...' : 'Khôi phục mặc định'}
          </button>
          <button type="submit" className="apple-btn active" disabled={saving || !isDirty}>
            {saving ? 'Đang áp dụng...' : 'Lưu & Áp dụng ngay'}
          </button>
        </div>
      </div>

      {err && <div className="error-banner">{err}</div>}
      {okMsg && <div className="success-banner">{okMsg}</div>}

      {/* 1. Chính sách Cảnh báo Leo thang L1 -> L2 -> L3 & Auto-Resolved */}
      <div className="apple-card">
        <div className="card-title-row">
          <div>
            <h3>1. Chính sách Cảnh báo Leo thang (L1 → L2 → L3) &amp; Hồi phục</h3>
            <p className="setting-desc">
              Áp dụng khi vận hành ở chế độ Chờ phê duyệt (Human-in-the-Loop). Mọi thay đổi có hiệu lực ngay.
            </p>
          </div>
          <div className="preset-row">
            {ESCALATION_PRESETS.map((p) => (
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

        {/* Sơ đồ trực quan hóa chuỗi thời gian L0 -> L1 -> L2 -> L3 -> Auto-Resolved */}
        <div className="escalation-timeline">
          <div className="timeline-node t0">
            <span>Tầng 0</span>
            <strong>Lọc nhiễu</strong>
            <small>TransientSpike · 0 tin</small>
          </div>
          <div className="timeline-arrow">→</div>
          <div className="timeline-node t1">
            <span>Tầng 1 (L1)</span>
            <strong>0 giây</strong>
            <small>Báo ngay + nút duyệt</small>
          </div>
          <div className="timeline-arrow">→</div>
          <div className="timeline-node t2">
            <span>Tầng 2 (L2)</span>
            <strong>Sau {l2}s</strong>
            <small>Nhắc &amp; leo thang cấp 2</small>
          </div>
          <div className="timeline-arrow">→</div>
          <div className="timeline-node t3">
            <span>Tầng 3 (L3)</span>
            <strong>Sau {l3}s</strong>
            <small>
              {form.l3_auto_mitigate
                ? `Tự bảo vệ (≥ ${form.l3_min_confidence_pct}%)`
                : 'Chỉ báo thủ công'}
            </small>
          </div>
          <div className="timeline-arrow">→</div>
          <div className="timeline-node t-ok">
            <span>Auto-Resolved</span>
            <strong>{cycles} chu kỳ</strong>
            <small>≈ {cycles * 2}s NORMAL liên tiếp</small>
          </div>
        </div>

        <div className="settings-grid-3">
          <label className="policy-field">
            <span>Thời gian leo thang L1 → L2 (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={600}
              value={form.escalation_l2_seconds}
              onChange={(e) => setField('escalation_l2_seconds', Number(e.target.value))}
            />
            <small className="setting-hint">Khoảng hợp lệ: 5 – 600 giây (mặc định 30s)</small>
          </label>

          <label className="policy-field">
            <span>Thời gian leo thang L2 → L3 (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={10}
              max={1800}
              value={form.escalation_l3_seconds}
              onChange={(e) => setField('escalation_l3_seconds', Number(e.target.value))}
            />
            <small className="setting-hint">Phải lớn hơn L2 ít nhất 5 giây (mặc định 60s)</small>
          </label>

          <label className="policy-field">
            <span>Số chu kỳ NORMAL xác nhận Auto-Resolved</span>
            <input
              type="number"
              className="apple-select"
              min={1}
              max={10}
              value={form.auto_resolved_cycles}
              onChange={(e) => setField('auto_resolved_cycles', Number(e.target.value))}
            />
            <small className="setting-hint">Mỗi chu kỳ đo là 2 giây (mặc định 2 chu kỳ ≈ 4s)</small>
          </label>
        </div>

        <div className="settings-divider" />

        <ToggleRow
          title="Tự động bảo vệ khẩn cấp ở Tầng 3 (Fail-Safe Auto-Mitigation)"
          desc="Khi quá hạn L3 mà chưa có phản hồi, hệ thống tự chặn IP tấn công để cứu dịch vụ. Riêng Unknown_Anomaly luôn bị khóa an toàn, tuyệt đối không tự chặn."
          checked={Boolean(form.l3_auto_mitigate)}
          onChange={(v) => setField('l3_auto_mitigate', v)}
        />

        <div className="settings-slider-row" style={{ opacity: form.l3_auto_mitigate ? 1 : 0.5 }}>
          <label className="policy-field" style={{ flex: 1 }}>
            <span>
              Độ tin cậy tối thiểu của Model 2 để Tầng 3 tự bảo vệ:{' '}
              <strong className="accent">{form.l3_min_confidence_pct}%</strong>
            </span>
            <input
              type="range"
              min={70}
              max={99}
              step={1}
              disabled={!form.l3_auto_mitigate}
              value={form.l3_min_confidence_pct}
              onChange={(e) => setField('l3_min_confidence_pct', Number(e.target.value))}
              style={{ width: '100%' }}
            />
            <small className="setting-hint">
              Khuyến nghị ≥ 80% để chỉ tự chặn khi XGBoost RCA chẩn đoán rất chắc chắn.
            </small>
          </label>
        </div>
      </div>

      {/* 2. AI Policy Gate & Cooldown chống lặp */}
      <div className="apple-card">
        <div className="card-title-row">
          <div>
            <h3>2. AI Policy Gate &amp; Bộ chống lặp (Cooldown)</h3>
            <p className="setting-desc">
              Điều chỉnh cổng kích hoạt ứng phó và thời gian nghỉ giữa các lần ghi cảnh báo/nhật ký.
            </p>
          </div>
        </div>

        <ToggleRow
          title="Chế độ Tự động ứng phó (Auto-Pilot)"
          desc="Bật: tự chặn ngay bằng iptables khi Model 2 chẩn đoán rõ tấn công. Tắt: chuyển sang Human-in-the-Loop (chờ phê duyệt trên Web/Telegram kèm leo thang L1–L3)."
          checked={Boolean(form.auto_pilot)}
          onChange={(v) => setField('auto_pilot', v)}
        />

        <div className="settings-slider-row">
          <label className="policy-field" style={{ flex: 1 }}>
            <span>
              Ngưỡng xác suất Dự báo 5 phút để kích hoạt Model 2 RCA:{' '}
              <strong className="accent">{form.forecast_threshold_pct}%</strong>
            </span>
            <input
              type="range"
              min={40}
              max={90}
              step={1}
              value={form.forecast_threshold_pct}
              onChange={(e) => setField('forecast_threshold_pct', Number(e.target.value))}
              style={{ width: '100%' }}
            />
            <small className="setting-hint">
              Mặc định 60%: lọc bỏ các dao động thoáng qua (TransientSpike) có xác suất thấp, chỉ gọi XGBoost RCA khi nguy cơ suy giảm thật.
            </small>
          </label>
        </div>

        <div className="settings-divider" />

        <div className="settings-grid-3">
          <label className="policy-field">
            <span>Cooldown chống lặp sự cố (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={120}
              value={form.incident_cooldown_seconds}
              onChange={(e) => setField('incident_cooldown_seconds', Number(e.target.value))}
            />
            <small className="setting-hint">Khoảng nghỉ tối thiểu giữa 2 lần tạo sự cố cùng dịch vụ (mặc định 12s)</small>
          </label>

          <label className="policy-field">
            <span>Thời gian tạm ẩn sau khi "Bỏ qua" (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={10}
              max={600}
              value={form.dismissed_cooldown_seconds}
              onChange={(e) => setField('dismissed_cooldown_seconds', Number(e.target.value))}
            />
            <small className="setting-hint">Không nhắc lại sự cố vừa bấm Bỏ qua trên Web/Telegram (mặc định 45s)</small>
          </label>

          <label className="policy-field">
            <span>Cooldown ghi log TransientSpike (giây)</span>
            <input
              type="number"
              className="apple-select"
              min={5}
              max={120}
              value={form.spike_log_cooldown_seconds}
              onChange={(e) => setField('spike_log_cooldown_seconds', Number(e.target.value))}
            />
            <small className="setting-hint">Tránh tràn bảng nhật ký khi mạng có gai nhiễu liên tiếp (mặc định 18s)</small>
          </label>
        </div>
      </div>

      {/* 3. Kênh Thông báo Telegram ChatOps & Alertmanager */}
      <div className="apple-card">
        <div className="card-title-row">
          <div>
            <h3>3. Kênh Thông báo Telegram ChatOps &amp; Alertmanager</h3>
            <p className="setting-desc">
              Bật/tắt từng luồng tin hoặc đổi Chat ID trực tiếp. Riêng <code>TELEGRAM_BOT_TOKEN</code> luôn được giữ bí mật trong <code>.env</code>.
            </p>
          </div>
          <div className="pending-actions">
            <span className={`tg-pill ${tgInfo.enabled ? 'on' : 'off'}`}>
              {tgInfo.enabled
                ? `Đã kết nối${tgInfo.bot_username ? ` (${tgInfo.bot_username})` : ''}`
                : tgInfo.bot_configured
                ? 'Thiếu Chat ID'
                : 'Chưa có Bot Token (.env)'}
            </span>
            {tgInfo.enabled && (
              <button
                type="button"
                className="apple-btn secondary"
                onClick={handleTestTelegram}
                disabled={testingTg}
              >
                {testingTg ? 'Đang gửi...' : 'Gửi tin thử Telegram'}
              </button>
            )}
          </div>
        </div>

        <ToggleRow
          title="Gửi cảnh báo sự cố AI (L1, L2, L3, Auto-Pilot, Unknown_Anomaly)"
          desc="Tắt tạm thời khi bảo trì hệ thống để không gửi tin nhắn ra điện thoại mà vẫn giữ nguyên giám sát trên Web."
          checked={Boolean(form.telegram_notify_ai)}
          onChange={(v) => setField('telegram_notify_ai', v)}
        />
        <ToggleRow
          title="Gửi cảnh báo Hạ tầng cứng từ Prometheus Alertmanager"
          desc="Chuyển tiếp các cảnh báo HostDown, WANLinkDown, HighCpuUsage, HighPacketRate ra kênh Telegram."
          checked={Boolean(form.telegram_notify_infra)}
          onChange={(v) => setField('telegram_notify_infra', v)}
        />
        <ToggleRow
          title="Gửi thông báo Hồi phục (Auto-Resolved)"
          desc="Gửi tin nhắn xanh xác nhận khi dịch vụ gặp sự cố đã quay về trạng thái NORMAL ổn định."
          checked={Boolean(form.telegram_notify_resolved)}
          onChange={(v) => setField('telegram_notify_resolved', v)}
        />

        <div className="settings-divider" />

        <div className="settings-grid-3">
          <label className="policy-field">
            <span>Telegram Chat ID chính (Tầng 1)</span>
            <input
              type="text"
              className="apple-select"
              placeholder="VD: 1234567890"
              value={form.telegram_chat_id || ''}
              onChange={(e) => setField('telegram_chat_id', e.target.value)}
            />
            <small className="setting-hint">Nhận cảnh báo L1, Auto-Pilot, Alertmanager &amp; Auto-Resolved</small>
          </label>

          <label className="policy-field">
            <span>Telegram Chat ID Leo thang Tầng 2/3</span>
            <input
              type="text"
              className="apple-select"
              placeholder="Để trống = dùng chung Chat ID chính"
              value={form.telegram_l2_chat_id || ''}
              onChange={(e) => setField('telegram_l2_chat_id', e.target.value)}
            />
            <small className="setting-hint">Có thể điền ID nhóm trực cấp 2 hoặc để trống khi demo 1 điện thoại</small>
          </label>

          <label className="policy-field">
            <span>Danh sách Telegram Admin IDs</span>
            <input
              type="text"
              className="apple-select"
              placeholder="VD: 1234567890, 9876543210"
              value={form.telegram_admin_ids || ''}
              onChange={(e) => setField('telegram_admin_ids', e.target.value)}
            />
            <small className="setting-hint">Các tài khoản được quyền bấm nút Phê duyệt / Bỏ qua / Gỡ chặn</small>
          </label>
        </div>
      </div>

      {/* 4. Giao diện & Thông số Mô hình AI (chỉ đọc) */}
      <div className="apple-card">
        <div className="card-title-row">
          <div>
            <h3>4. Nhịp cập nhật Giao diện &amp; Thông số Mô hình AI (Chỉ đọc)</h3>
            <p className="setting-desc">
              Ngưỡng phân mức của 2 mô hình AI được hiệu chuẩn từ tập dữ liệu thực nghiệm và khóa cố định để đảm bảo độ chính xác.
            </p>
          </div>
        </div>

        <div className="settings-grid-2">
          <label className="policy-field">
            <span>Chu kỳ làm mới dữ liệu trên Web Dashboard</span>
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
            <small className="setting-hint">Áp dụng tức thì cho các biểu đồ và trạng thái thời gian thực</small>
          </label>

          {modelInfo && (
            <div className="model-readonly-box">
              <div>
                <strong>Model 1 — Extended Isolation Forest ({modelInfo.model1_features} đặc trưng):</strong>{' '}
                <code>MED ≥ {modelInfo.model1_thresholds?.MEDIUM}</code> ·{' '}
                <code>HIGH ≥ {modelInfo.model1_thresholds?.HIGH}</code> ·{' '}
                <code>CRIT ≥ {modelInfo.model1_thresholds?.CRITICAL}</code>
              </div>
              <div>
                <strong>Model 2 — XGBoost RCA ({modelInfo.model2_features} đặc trưng):</strong>{' '}
                Ngưỡng tự tin <code>≥ {modelInfo.model2_confidence_threshold_pct}%</code> ·{' '}
                Lớp: <code>{(modelInfo.model2_classes || []).join(', ')}</code>
              </div>
            </div>
          )}
        </div>
      </div>
    </form>
  );
}
