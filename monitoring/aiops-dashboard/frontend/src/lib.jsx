import React from 'react';

/** 4 mức độ chuẩn dùng thống nhất toàn giao diện. */
export const SEVERITY = {
  NORMAL: { label: 'Bình thường', rank: 0 },
  MEDIUM: { label: 'Trung bình', rank: 1 },
  HIGH: { label: 'Cao', rank: 2 },
  CRITICAL: { label: 'Nghiêm trọng', rank: 3 },
};

export const normSev = (s) => (SEVERITY[s] ? s : 'NORMAL');
export const sevLabel = (s) => SEVERITY[s]?.label || s || 'Bình thường';
export const sevRank = (s) => SEVERITY[s]?.rank ?? 0;

export function StatusPill({ level, big = false }) {
  const lv = SEVERITY[level] ? level : 'NORMAL';
  return <span className={`sev-pill sev-${lv} ${big ? 'big' : ''}`}>{sevLabel(lv)}</span>;
}

export const FAULT_LABELS = {
  SynFlood: 'Tấn công SYN Flood',
  HttpFlood: 'Tấn công HTTP Flood',
  SshBruteforce: 'Dò mật khẩu SSH',
  PortScan: 'Quét cổng (Port Scan)',
  TransientSpike: 'Gai nhiễu thoáng qua',
  Unknown_Anomaly: 'Bất thường chưa xác định',
  Normal: 'Không có sự cố',
  'Normal (Standby)': 'Không có sự cố',
};

export const faultLabel = (f) => {
  if (!f) return 'Không có sự cố';
  if (FAULT_LABELS[f]) return FAULT_LABELS[f];
  if (typeof f === 'string' && f.includes(',')) {
    return f
      .split(',')
      .map((part) => {
        const k = part.trim();
        return FAULT_LABELS[k] || k;
      })
      .filter(Boolean)
      .join(', ');
  }
  return f;
};

export function getRuleCommands(rule) {
  if (!rule) return [];
  if (Array.isArray(rule.commands) && rule.commands.length > 0) {
    return rule.commands.filter(Boolean);
  }
  return String(rule.command || '')
    .split('&&')
    .map((c) => c.trim())
    .filter(Boolean);
}

export function worstLevel(evals) {
  let worst = 'NORMAL';
  Object.values(evals || {}).forEach((e) => {
    if (sevRank(e.severity_level) > sevRank(worst)) worst = e.severity_level;
  });
  return worst;
}
