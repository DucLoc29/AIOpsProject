function renderDashboard(data) {
  // 1. Global Status Pill
  const pill = document.getElementById('global-status-pill');
  const statusText = document.getElementById('global-status-text');
  pill.classList.remove('alert', 'warn');

  if (data.policy_gate.trigger_model3) {
    pill.classList.add('alert');
    statusText.textContent = `🚨 SỰ CỐ: ${data.model3.fault_class.toUpperCase()} (${data.model3.confidence_pct}%)`;
  } else if (data.model1.is_degraded) {
    pill.classList.add('warn');
    statusText.textContent = `⚠️ GAI NHIỄU THOÁNG QUA (ĐÃ LỌC BỞI MODEL 2)`;
  } else {
    statusText.textContent = `🟢 ĐANG GIÁM SÁT (${data.scenario_active.toUpperCase()})`;
  }

  // 2. Highlight active scenario button
  document.querySelectorAll('.scen-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.scen === data.scenario_active);
  });

  // 3. Model 1 Card (Extended Isolation Forest)
  const cardM1 = document.getElementById('card-model1');
  document.getElementById('m1-score').textContent = data.model1.anomaly_score.toFixed(4);
  document.getElementById('m1-thresh').textContent = data.model1.threshold.toFixed(4);
  const m1Pct = Math.min(100, Math.max(8, Math.round((data.model1.anomaly_score / 0.8) * 100)));
  const m1Bar = document.getElementById('m1-bar');
  m1Bar.style.width = `${m1Pct}%`;
  m1Bar.style.backgroundColor = data.model1.is_degraded ? '#ef4444' : '#10b981';

  const m1Verdict = document.getElementById('m1-verdict');
  cardM1.classList.toggle('alert-border', data.model1.is_degraded);
  if (data.model1.is_degraded) {
    m1Verdict.className = 'stage-verdict danger';
    m1Verdict.textContent = '🔴 BẤT THƯỜNG TỨC THỜI (current_degraded = 1)';
  } else {
    m1Verdict.className = 'stage-verdict normal';
    m1Verdict.textContent = '🟢 BÌNH THƯỜNG (current_degraded = 0)';
  }

  // 4. Model 2 Card (XGBoost 5m Forecaster)
  const cardM2 = document.getElementById('card-model2');
  document.getElementById('m2-prob').textContent = `${data.model2.prob_5m_pct.toFixed(2)}%`;
  document.getElementById('m2-thresh').textContent = `${data.model2.threshold_pct}%`;
  const m2Bar = document.getElementById('m2-bar');
  m2Bar.style.width = `${Math.min(100, data.model2.prob_5m_pct)}%`;
  m2Bar.style.backgroundColor = data.model2.is_sustained ? '#ef4444' : '#10b981';

  const m2Verdict = document.getElementById('m2-verdict');
  cardM2.classList.toggle('alert-border', data.model2.is_sustained);
  if (data.model2.is_sustained) {
    m2Verdict.className = 'stage-verdict danger';
    m2Verdict.textContent = `🔴 DỰ BÁO KÉO DÀI 5 PHÚT (>= ${data.model2.threshold_pct}%)`;
  } else if (data.model1.is_degraded) {
    m2Verdict.className = 'stage-verdict warn';
    m2Verdict.textContent = `🟡 GAI NHIỄU TỰ TẮT (< ${data.model2.threshold_pct}%)`;
  } else {
    m2Verdict.className = 'stage-verdict normal';
    m2Verdict.textContent = '🟢 DỰ BÁO ỔN ĐỊNH (Không suy giảm)';
  }

  // 5. System Policy Gate Card
  const cardGate = document.getElementById('card-gate');
  const c1 = document.getElementById('gate-c1');
  const c2 = document.getElementById('gate-c2');
  c1.textContent = data.model1.is_degraded ? 'M1: BẤT THƯỜNG (1)' : 'M1: BÌNH THƯỜNG (0)';
  c1.classList.toggle('on', data.model1.is_degraded);
  c2.textContent = `M2 >= ${data.model2.threshold_pct}%: ${data.model2.is_sustained ? 'ĐẠT' : 'KHÔNG'}`;
  c2.classList.toggle('on', data.model2.is_sustained);

  const gateVerdict = document.getElementById('gate-verdict');
  cardGate.classList.toggle('alert-border', data.policy_gate.trigger_model3);
  cardGate.classList.toggle('warn-border', !data.policy_gate.trigger_model3 && data.model1.is_degraded);
  if (data.policy_gate.trigger_model3) {
    gateVerdict.className = 'stage-verdict danger';
    gateVerdict.textContent = '🚀 KÍCH HOẠT MODEL 3 (Đủ 2 điều kiện)';
  } else if (data.model1.is_degraded) {
    gateVerdict.className = 'stage-verdict warn';
    gateVerdict.textContent = '🛡️ ĐÃ CHẶN BÁO ĐỘNG GIẢ (Không gọi Model 3)';
  } else {
    gateVerdict.className = 'stage-verdict normal';
    gateVerdict.textContent = '⏸ KHÔNG GỌI MODEL 3 (Mạng ổn định)';
  }

  // Pending Manual Approval Box
  const appBox = document.getElementById('approval-box');
  if (data.network_state.pending_approval) {
    appBox.classList.remove('hidden');
    document.getElementById('approval-detail').textContent =
      `${data.network_state.pending_approval.fault_class}: ${data.network_state.pending_approval.remediation.title}`;
  } else {
    appBox.classList.add('hidden');
  }

  // 6. Model 3 Card (XGBoost Multi-class RCA)
  const cardM3 = document.getElementById('card-model3');
  cardM3.classList.toggle('alert-border', data.model3.invoked);
  document.getElementById('m3-class').textContent = data.model3.invoked
    ? data.model3.fault_class
    : 'Standby (Chưa gọi)';
  document.getElementById('m3-conf').textContent = data.model3.invoked
    ? `${data.model3.confidence_pct.toFixed(1)}%`
    : '—';
  document.getElementById('m3-rem-title').textContent = data.model3.remediation.title;
  document.getElementById('m3-rem-cmd').textContent = data.model3.remediation.command;

  // 7. Telemetry Snapshot
  const t = data.telemetry;
  document.getElementById('t-wan1').textContent = `${t.wan1_rx_mbps} / ${t.wan1_tx_mbps} Mbps`;
  document.getElementById('t-retrans').textContent = `${t.wan_retrans_rate} / ${t.wan_syn_retrans} pkt/s`;
  document.getElementById('t-fw').textContent = `${t.fw_rx_pkts_rate} pkt/s`;
  document.getElementById('t-lan').textContent = `${t.client_lan_tx_pkts} pkt/s`;
  document.getElementById('t-rst').textContent = `${t.srv_tcp_rst_rate} pkt/s`;
  document.getElementById('t-cpu').textContent = `${t.srv_cpu_usage} %`;
  document.getElementById('t-nginx').textContent = `${t.nginx_req_rate} req/s`;
  document.getElementById('t-api').textContent = `${t.api_latency_ms} ms`;

  document.getElementById('wan-route-badge').textContent =
    `Đường truyền hiện tại: ${data.network_state.wan_active_path}`;
  document.getElementById('blocked-ips-text').textContent =
    data.network_state.blocked_ips.length > 0
      ? data.network_state.blocked_ips.join(' · ')
      : 'Không có IP nào bị chặn';

  // 8. 7-Class Probability Bars
  const probContainer = document.getElementById('class-prob-bars');
  probContainer.innerHTML = '';
  const probs = data.model3.class_probabilities || {};
  Object.entries(probs)
    .sort((a, b) => b[1] - a[1])
    .forEach(([cls, pct]) => {
      const displayPct = data.model3.invoked ? pct : 0.0;
      const row = document.createElement('div');
      row.className = 'prob-row';
      row.innerHTML = `
        <span><strong>${cls}</strong></span>
        <div class="prob-bar-bg">
          <div class="prob-bar-fill" style="width: ${displayPct}%; background: ${displayPct > 50 ? '#ef4444' : '#38bdf8'}"></div>
        </div>
        <span style="text-align:right">${data.model3.invoked ? pct.toFixed(1) + '%' : 'Standby'}</span>
      `;
      probContainer.appendChild(row);
    });

  // 9. Remediation Audit Logs
  const tbody = document.getElementById('audit-tbody');
  tbody.innerHTML = '';
  (data.audit_logs || []).forEach((item) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${item.time}</code></td>
      <td><strong>${item.scenario}</strong></td>
      <td>${item.trigger}</td>
      <td><strong>${item.fault_class}</strong></td>
      <td>${item.confidence}</td>
      <td><code>${item.target}</code></td>
      <td>${item.action}</td>
      <td><span class="status-tag ${item.status}">${item.status}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

async function fetchState() {
  const res = await fetch('/api/state');
  const data = await res.json();
  renderDashboard(data);
}

// Gắn sự kiện nút kịch bản
document.querySelectorAll('.scen-btn').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const scenario = btn.dataset.scen;
    const res = await fetch('/api/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario }),
    });
    const data = await res.json();
    renderDashboard(data);
  });
});

// Thanh trượt ngưỡng dự báo 5m
const slider = document.getElementById('threshold-slider');
const threshLabel = document.getElementById('thresh-label');
slider.addEventListener('input', async () => {
  threshLabel.textContent = `${slider.value}%`;
  const res = await fetch('/api/policy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ threshold: Number(slider.value) }),
  });
  renderDashboard(await res.json());
});

// Công tắc Auto-Pilot vs Human-in-the-Loop
const autoToggle = document.getElementById('autopilot-toggle');
const autoLabel = document.getElementById('autopilot-label');
autoToggle.addEventListener('change', async () => {
  const isAuto = autoToggle.checked;
  autoLabel.className = isAuto ? 'mode-badge auto' : 'mode-badge manual';
  autoLabel.textContent = isAuto
    ? '🤖 AUTO-PILOT (Tự động Khắc phục)'
    : '👤 HUMAN-IN-THE-LOOP (Chờ Phê duyệt)';
  const res = await fetch('/api/policy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_pilot: isAuto }),
  });
  renderDashboard(await res.json());
});

// Nút Phê duyệt thủ công
document.getElementById('approve-btn').addEventListener('click', async () => {
  const res = await fetch('/api/approve', { method: 'POST' });
  renderDashboard(await res.json());
});

// Nút Rollback khôi phục mạng
document.getElementById('rollback-btn').addEventListener('click', async () => {
  const res = await fetch('/api/rollback', { method: 'POST' });
  renderDashboard(await res.json());
});

// Khởi chạy ban đầu và tự động làm mới mỗi 3 giây
fetchState();
setInterval(fetchState, 3000);

