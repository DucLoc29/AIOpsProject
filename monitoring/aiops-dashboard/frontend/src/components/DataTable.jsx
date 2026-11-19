import React from 'react';

export function Badge({ value }) {
  return <span className={`badge-status ${String(value).replace(/\s/g, '_')}`}>{value}</span>;
}

/** Bảng dữ liệu tổng quát: columns = [{ key, label, render? }] */
export default function DataTable({ columns, rows, empty = 'Chưa có bản ghi nào.' }) {
  return (
    <div className="table-scroll">
      <table className="apple-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="empty-cell">
                {empty}
              </td>
            </tr>
          )}
          {rows.map((row, i) => (
            <tr key={row.action_id || row.alert_id || row.result_id || row.rule_id || row.service_id || i}>
              {columns.map((c) => (
                <td key={c.key}>{c.render ? c.render(row) : row[c.key]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const Code = ({ children }) => <code>{children}</code>;

