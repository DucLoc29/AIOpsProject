import React from 'react';

/** 4 mức độ chuẩn dùng thống nhất toàn giao diện. */
export const SEVERITY = {
  NORMAL: { label: 'Bình thường', rank: 0 },
  MEDIUM: { label: 'Trung bình', rank: 1 },
  HIGH: { label: 'Cao', rank: 2 },
  CRITICAL: { label: 'Nghiêm trọng', rank: 3 },
};

export const sevLabel = (s) => SEVERITY[s]?.label || s || 'Bình thường';
export const sevRank = (s) => SEVERITY[s]?.rank ?? 0;

export function StatusPill({ level, big = false }) {
  const lv = SEVERITY[level] ? level : 'NORMAL';
  return <span className={`sev-pill sev-${lv} ${big ? 'big' : ''}`}>{sevLabel(lv)}</span>;
}

/** Kịch bản mô phỏng để demo (đã bỏ WAN). */
export const SCENARIOS = [
  { id: 'Normal', label: 'Bình thường' },
  { id: 'TransientSpike', label: 'Gai nhiễu thoáng qua' },
  { id: 'SynFlood', label: 'SYN Flood' },
  { id: 'HttpFlood', label: 'HTTP Flood' },
  { id: 'SshBruteforce', label: 'SSH Brute-force' },
  { id: 'PortScan', label: 'Port Scan nội bộ' },
];

export const FAULT_LABELS = {
  SynFlood: 'Tấn công SYN Flood',
  HttpFlood: 'Tấn công HTTP Flood',
  SshBruteforce: 'Dò mật khẩu SSH',
  PortScan: 'Quét cổng (Port Scan)',
  Normal: 'Không có sự cố',
  'Normal (Standby)': 'Không có sự cố',
};

export const faultLabel = (f) => FAULT_LABELS[f] || f || 'Không có sự cố';

export function worstLevel(evals) {
  let worst = 'NORMAL';
  Object.values(evals || {}).forEach((e) => {
    if (sevRank(e.severity_level) > sevRank(worst)) worst = e.severity_level;
  });
  return worst;
}
