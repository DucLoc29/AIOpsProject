import React from 'react';

export const DEFAULT_SERVICE_OPTIONS = [
  { id: 'srv_web_portal', name: 'Corporate Web Portal (192.168.10.10:80)' },
  { id: 'srv_flask_api', name: 'Employee & Auth REST API (192.168.10.20:5000)' },
  { id: 'srv_postgres_db', name: 'Corporate Database (192.168.10.21:5432)' },
  { id: 'srv_ssh_access', name: 'Remote Admin SSH (192.168.10.10:22)' },
];

export const SERVICE_OPTIONS = DEFAULT_SERVICE_OPTIONS;

const CHART_CONFIGS = [
  {
    key: 'latency',
    title: 'Độ trễ (Latency)',
    unit: 'ms',
    color: '#2563eb',
    gradId: 'grad-blue',
    minCeiling: 6,
  },
  {
    key: 'jitter',
    title: 'Độ giật (Jitter)',
    unit: 'ms',
    color: '#10b981',
    gradId: 'grad-green',
    minCeiling: 2.4,
  },
  {
    key: 'packet_loss',
    title: 'Rớt gói (Packet Loss)',
    unit: '%',
    color: '#ef4444',
    gradId: 'grad-red',
    minCeiling: 0.4,
  },
  {
    key: 'throughput',
    title: 'Thông lượng (Throughput)',
    unit: ' KB/s',
    color: '#f59e0b',
    gradId: 'grad-amber',
    minCeiling: 40,
  },
  {
    key: 'response_time',
    title: 'Thời gian phản hồi (Response Time)',
    unit: 'ms',
    color: '#8b5cf6',
    gradId: 'grad-purple',
    minCeiling: 10,
  },
  {
    key: 'uptime',
    title: 'Khả dụng (Uptime)',
    unit: '%',
    color: '#0d9488',
    gradId: 'grad-teal',
    minCeiling: 100,
    isUptime: true,
  },
];

function buildSmoothSvgPath(points) {
  if (!points || points.length === 0) return { line: '', area: '' };
  if (points.length === 1) {
    const p = points[0];
    return {
      line: `M ${p.x} ${p.y}`,
      area: `M ${p.x} 165 L ${p.x} ${p.y} L ${p.x} 165 Z`,
    };
  }
  let d = `M ${points[0].x.toFixed(1)} ${points[0].y.toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i];
    const p1 = points[i + 1];
    const cx = ((p0.x + p1.x) / 2).toFixed(1);
    d += ` C ${cx} ${p0.y.toFixed(1)}, ${cx} ${p1.y.toFixed(1)}, ${p1.x.toFixed(1)} ${p1.y.toFixed(1)}`;
  }
  const first = points[0];
  const last = points[points.length - 1];
  const area = `${d} L ${last.x.toFixed(1)} 165 L ${first.x.toFixed(1)} 165 Z`;
  return { line: d, area };
}

function computeAdaptiveRange(values, cfg) {
  if (cfg.isUptime) {
    const minVal = Math.min(100, ...values);
    const yMin = minVal >= 98.5 ? 95 : Math.max(0, Math.floor((minVal - 4) / 5) * 5);
    return { yMin, yMax: 100 };
  }
  const maxVal = Math.max(0, ...values);
  const target = Math.max(cfg.minCeiling, maxVal * 1.28);
  let yMax = target;
  if (target <= 1) yMax = Math.ceil(target * 10) / 10;
  else if (target <= 5) yMax = Math.ceil(target * 2) / 2;
  else if (target <= 20) yMax = Math.ceil(target / 2) * 2;
  else yMax = Math.ceil(target / 4) * 4;
  return { yMin: 0, yMax };
}

function SingleAreaChart({ cfg, history, serviceId }) {
  const defaultVal = cfg.isUptime ? 100 : 0;
  const values = history.map((h) => Number(h.services?.[serviceId]?.[cfg.key] ?? defaultVal));
  const times = history.map((h) => h.time || '');
  const { yMin, yMax } = computeAdaptiveRange(values, cfg);
  const span = Math.max(0.001, yMax - yMin);
  const ticks = [yMax, yMin + (span * 3) / 4, yMin + span / 2, yMin + span / 4, yMin];

  const leftPad = 54;
  const rightPad = 14;
  const topPad = 14;
  const bottomY = 165;
  const plotW = 520 - leftPad - rightPad;
  const plotH = bottomY - topPad;

  const pts = values.map((v, idx) => {
    const x =
      values.length > 1
        ? leftPad + (idx / (values.length - 1)) * plotW
        : leftPad + plotW / 2;
    const clamped = Math.max(yMin, Math.min(yMax, v));
    const y = bottomY - ((clamped - yMin) / span) * plotH;
    return { x, y, v };
  });

  const { line, area } = buildSmoothSvgPath(pts);
  const latestVal = values.length ? values[values.length - 1] : defaultVal;
  const prevVal = values.length > 1 ? values[values.length - 2] : latestVal;
  const delta = latestVal - prevVal;
  const avgVal = values.length ? values.reduce((a, b) => a + b, 0) / values.length : defaultVal;
  const lastPt = pts.length ? pts[pts.length - 1] : null;

  return (
    <div className="perf-chart-card">
      <div className="perf-chart-header">
        <div>
          <h4>{cfg.title}</h4>
          <span className="perf-current-val" style={{ color: cfg.color }}>
            Hiện tại: {latestVal.toFixed(2)}{cfg.unit}{' '}
            <small style={{ color: '#86868b', fontWeight: 500, marginLeft: 4 }}>
              ({delta >= 0 ? `+${delta.toFixed(2)}` : delta.toFixed(2)} · TB 5 mẫu: {avgVal.toFixed(2)}{cfg.unit})
            </small>
          </span>
        </div>
        <span className="live-badge">
          <span className="live-dot" style={{ background: cfg.color }} />
          LIVE 2s
        </span>
      </div>

      <svg viewBox="0 0 520 195" className="perf-svg">
        <defs>
          <linearGradient id={cfg.gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={cfg.color} stopOpacity="0.28" />
            <stop offset="100%" stopColor={cfg.color} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {ticks.map((t, i) => {
          const y = topPad + (i / (ticks.length - 1)) * plotH;
          const decimals = span <= 2 ? 2 : span <= 12 ? 1 : 0;
          const label = `${t.toFixed(decimals)}${cfg.unit.trim()}`;
          return (
            <g key={i}>
              <line
                x1={leftPad}
                y1={y}
                x2={520 - rightPad}
                y2={y}
                stroke="#e5e7eb"
                strokeDasharray="3 3"
                strokeWidth="1"
              />
              <text
                x={leftPad - 8}
                y={y + 4}
                textAnchor="end"
                fontSize="10"
                fill="#86868b"
              >
                {label}
              </text>
            </g>
          );
        })}

        {area && <path d={area} fill={`url(#${cfg.gradId})`} />}
        {line && (
          <path
            d={line}
            fill="none"
            stroke={cfg.color}
            strokeWidth="2.6"
            strokeLinecap="round"
          />
        )}

        {pts.map((p, idx) => (
          <circle
            key={idx}
            cx={p.x}
            cy={p.y}
            r={idx === pts.length - 1 ? 4.2 : 2.3}
            fill={idx === pts.length - 1 ? cfg.color : '#ffffff'}
            stroke={cfg.color}
            strokeWidth="1.8"
          />
        ))}
        {lastPt && (
          <circle
            cx={lastPt.x}
            cy={lastPt.y}
            r="8"
            fill={cfg.color}
            fillOpacity="0.22"
          />
        )}

        {times.map((tm, idx) => {
          if (idx % 2 !== 0 && idx !== times.length - 1) return null;
          const x =
            times.length > 1
              ? leftPad + (idx / (times.length - 1)) * plotW
              : leftPad + plotW / 2;
          return (
            <text
              key={idx}
              x={x}
              y={186}
              textAnchor="middle"
              fontSize="10"
              fill="#86868b"
            >
              {tm}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

export default function PerformanceCharts({ history = [], selectedService }) {
  const latest = history.length ? history[history.length - 1] : null;
  return (
    <div className="perf-section">
      <div className="section-title">
        <h3>6 chỉ số chất lượng dịch vụ</h3>
        <span>Cập nhật mỗi 2 giây · lúc {latest?.time || '--:--:--'}</span>
      </div>
      <div className="perf-grid-2x2">
        {CHART_CONFIGS.map((cfg) => (
          <SingleAreaChart key={cfg.key} cfg={cfg} history={history} serviceId={selectedService} />
        ))}
      </div>
    </div>
  );
}
