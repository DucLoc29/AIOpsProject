import React, { useEffect, useMemo, useState } from 'react';

export function Badge({ value }) {
  return <span className={`badge-status ${String(value).replace(/\s/g, '_')}`}>{value}</span>;
}

function buildPageNumbers(current, total) {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }
  const pages = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);
  if (left > 2) pages.push('...');
  for (let p = left; p <= right; p += 1) pages.push(p);
  if (right < total - 1) pages.push('...');
  pages.push(total);
  return pages;
}

/**
 * Bảng dữ liệu tổng quát hỗ trợ phân trang (không bị nhảy trang khi dữ liệu live cập nhật mỗi 2s).
 * Props:
 *   - columns: [{ key, label, render? }]
 *   - rows: Array<object>
 *   - empty: chuỗi hiển thị khi không có dữ liệu
 *   - pageSize: số dòng mặc định trên mỗi trang (mặc định 10)
 *   - pageSizeOptions: các tùy chọn số dòng/trang
 *   - resetKey: thay đổi khi người dùng đổi bộ lọc/tìm kiếm -> tự quay về trang 1
 *   - paginate: bật/tắt phân trang (mặc định true)
 */
export default function DataTable({
  columns,
  rows = [],
  empty = 'Chưa có bản ghi nào.',
  pageSize: initialPageSize = 10,
  pageSizeOptions = [5, 10, 20, 50],
  resetKey = '',
  paginate = true,
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialPageSize);

  useEffect(() => {
    setPage(1);
  }, [resetKey, pageSize]);

  const totalRows = rows.length;
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  const safePage = Math.min(page, totalPages);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const visibleRows = useMemo(() => {
    if (!paginate) return rows;
    const start = (safePage - 1) * pageSize;
    return rows.slice(start, start + pageSize);
  }, [rows, paginate, safePage, pageSize]);

  const startIdx = totalRows === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const endIdx = Math.min(totalRows, safePage * pageSize);
  const pageNumbers = useMemo(() => buildPageNumbers(safePage, totalPages), [safePage, totalPages]);

  const sizeOptions = useMemo(() => {
    const s = new Set(pageSizeOptions);
    s.add(initialPageSize);
    return Array.from(s).sort((a, b) => a - b);
  }, [pageSizeOptions, initialPageSize]);

  return (
    <>
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
            {visibleRows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="empty-cell">
                  {empty}
                </td>
              </tr>
            )}
            {visibleRows.map((row, i) => (
              <tr
                key={
                  row.action_id ||
                  row.alert_id ||
                  row.result_id ||
                  row.rule_id ||
                  row.service_id ||
                  row.username ||
                  `${safePage}-${i}`
                }
              >
                {columns.map((c) => (
                  <td key={c.key}>{c.render ? c.render(row) : row[c.key]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {paginate && totalRows > 0 && (
        <div className="table-pagination">
          <div className="table-page-info">
            <span>
              Hiển thị <b>{startIdx}–{endIdx}</b> / <b>{totalRows}</b> bản ghi
            </span>
            <select
              className="apple-select page-size-select"
              aria-label="Số dòng mỗi trang"
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
            >
              {sizeOptions.map((n) => (
                <option key={n} value={n}>
                  {n} dòng / trang
                </option>
              ))}
            </select>
          </div>

          <div className="table-page-controls">
            <button
              type="button"
              className="page-btn"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ← Trước
            </button>

            {pageNumbers.map((item, idx) =>
              item === '...' ? (
                <span key={`e-${idx}`} className="page-ellipsis">
                  …
                </span>
              ) : (
                <button
                  key={item}
                  type="button"
                  className={`page-btn ${item === safePage ? 'active' : ''}`}
                  onClick={() => setPage(item)}
                >
                  {item}
                </button>
              )
            )}

            <button
              type="button"
              className="page-btn"
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              Sau →
            </button>
          </div>
        </div>
      )}
    </>
  );
}

export const Code = ({ children }) => <code>{children}</code>;
