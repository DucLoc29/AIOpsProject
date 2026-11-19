# HANDOFF – Dự án AIOps (Đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 4 → 5 → 6. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-11-19. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Đề tài và yêu cầu

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

**5 yêu cầu thực hành của đề cương (Đã hoàn thành 5/5):**
1. Thiết kế, triển khai mô phỏng topology mạng doanh nghiệp trên môi trường Containerlab / Linux containers.
2. Xây dựng pipeline thu thập số liệu thời gian thực bằng **Prometheus**, **Active Service Probe** và dashboard **Grafana**.
3. Xây dựng kịch bản mô phỏng các tình huống tấn công, sự cố dịch vụ Server để thu thập dữ liệu **6 thông số SLA chuẩn + Cửa sổ trượt** và gán nhãn.
4. Huấn luyện và triển khai kiến trúc **2 Mô hình AI + 1 Công thức Dự báo Chuỗi thời gian** dùng Cửa sổ trượt (Sliding Window) phục vụ từng Dịch vụ riêng lẻ:
   - **Bước 1 — Model 1 (`model1_eif_detector.pkl` · Extended Isolation Forest dùng Cửa sổ trượt 18 features):** Đánh giá sức khỏe hiện tại theo **4 mức độ (`Bình thường / NORMAL`, `Trung bình / MEDIUM`, `Cao / HIGH`, `Nghiêm trọng / CRITICAL`)**, chống báo động giả bởi 1 dòng nhiễu đơn lẻ.
   - **Bước 2 — Công thức Dự báo Xu hướng 5 phút tới (`S_hat = EMA(S_t) + alpha * Slope * R_sustained`):** Ngoại suy toán học trực tiếp trên cửa sổ trượt để dự báo **4 mức độ tương lai** (không cần train mô hình ML dư thừa).
   - **Bước 3 — Model 2 (`model2_rca_classifier.pkl` · XGBoost Multi-class RCA dùng Cửa sổ trượt 36 features):** Phân loại chính xác **5 lớp (`Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`)** đạt **`Accuracy = 100%`**.
5. Xây dựng Trung tâm Chỉ huy AIOps Web Console (**React + Vite** chuẩn Apple Light Mode + Sidebar, kết nối **Cloud MongoDB Atlas**), hỗ trợ **Quản lý Dịch vụ Động (CRUD Services: Thêm / Sửa / Xóa)**, hiển thị **Lưới 6 Biểu đồ SLA** và **Tự động Ứng phó Hướng Dịch vụ theo đúng `-d <Service_IP> --dport <Service_Port>`** (không xung đột chéo, hỗ trợ Rollback từng dịch vụ bằng `iptables -D`).

---

## 2. Môi trường & Quy trình Triển khai (Local VM $\rightarrow$ Git $\rightarrow$ Azure VM)

| Mục | Giá trị |
|---|---|
| Nền tảng | Ubuntu Linux VM (Local & Azure VM đồng nhất hệ điều hành), Docker 29.x, Containerlab 0.81.x |
| Thư mục dự án | `/home/dash/Desktop/AIOpsProject` (Local VM) $\rightarrow$ `git push` $\rightarrow$ `~/aiops-project` (Azure VM) |
| Git Repo | `https://github.com/DucLoc29/AIOpsProject.git` (nhánh `main`) |
| Cloud Database | **MongoDB Atlas** (`cluster0.tbxyfbc.mongodb.net` · Database: `AIOpsProject` · 5 Collections) |
| Giao diện quản lý | **AIOps Command Center**: `http://localhost:5000` · **Grafana**: `http://localhost:3000` (admin/admin) · **Prometheus**: `http://localhost:9090` · **Alertmanager**: `http://localhost:9093` |

### ⚠ Quy trình khởi động Lab & AIOps Dashboard
Containerlab không tự động lưu trạng thái sau khi máy reboot. Mỗi lần khởi động chạy:
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```

Nếu chỉ muốn bật/khởi động lại riêng Web Server AIOps Command Center (`:5000`):
```bash
pkill -f "aiops-dashboard/backend/app.py" 2>/dev/null || true
nohup python3 monitoring/aiops-dashboard/backend/app.py >/tmp/aiops_dashboard.log 2>&1 &
```

**Frontend (React 18 + Vite 5):**
* Bản build sản phẩm (`monitoring/aiops-dashboard/frontend/dist/`) đã được biên dịch sẵn.
* Nếu sửa code trong `monitoring/aiops-dashboard/frontend/src/`, build lại bằng:
  ```bash
  /home/dash/.local/node/bin/node /home/dash/Desktop/AIOpsProject/monitoring/aiops-dashboard/frontend/node_modules/vite/bin/vite.js build /home/dash/Desktop/AIOpsProject/monitoring/aiops-dashboard/frontend
  ```

---

## 3. Chi tiết Kiến trúc Đã Chuẩn hóa (Giai đoạn 1 $\rightarrow$ 5)

### ✅ 3.1. Bộ 6 Thông số SLA Chuẩn hóa Đồng nhất cho MỌI Dịch vụ (Universal Service Telemetry)
Để phục vụ việc **Thêm / Sửa / Xóa dịch vụ động (`Services` CRUD)**, mọi dịch vụ trong bảng `Services` đều được thu thập đồng nhất **6 thông số SLA chuẩn** (hiển thị trên lưới 6 biểu đồ LIVE 2s) + **3 chữ ký gói tin Host**:
1. **`latency` (ms):** Độ trễ kết nối mạng (`TCP Connect / RTT`) tới `address:port` của dịch vụ.
2. **`jitter` (ms):** Độ biến thiên độ trễ giữa các chu kỳ lấy mẫu liên tiếp.
3. **`packet_loss` (%):** Tỷ lệ rớt gói / kết nối thất bại tới dịch vụ.
4. **`throughput` (KB/s):** Thông lượng dữ liệu thực tế đi qua dịch vụ.
5. **`response_time` (ms):** Thời gian phản hồi thực tế của ứng dụng tại cổng dịch vụ.
6. **`uptime` (%):** Tỷ lệ sẵn sàng / Độ khả dụng của dịch vụ (Availability SLA `0% – 100%`).
*(Kèm 3 đặc trưng chữ ký dưới nền từ Host/Prometheus: `resp_to_lat_ratio = response_time / latency`, `avg_pkt_size`, `tcp_rst_rate`).*

### ✅ 3.2. Tầng Cửa sổ trượt (Sliding Window 5 bước) & Kiến trúc AI 3 Bước theo Dịch vụ
Ngay tại tầng tiền xử lý ([`ai/collector/prepare_two_stage_datasets.py`](ai/collector/prepare_two_stage_datasets.py)), từ **thuần túy 6 thông số SLA gốc**, mỗi thông số được tính **5 đại lượng Cửa sổ trượt 5 bước** (`_roll_mean`, `_roll_std`, `_delta1`, `_trend_slope`, `_sustained_ratio` $\rightarrow$ `6 thông số × 6 đại lượng = 36 đặc trưng`), tạo thành dataset hợp nhất [`ai/dataset/aiops_network_dataset.csv`](ai/dataset/aiops_network_dataset.csv) (**669 mẫu × 44 cột**):
1. **Bước 1 — Model 1 ([`ai/models/model1_eif_detector.pkl`](ai/models/model1_eif_detector.pkl) — Extended Isolation Forest dùng Cửa sổ trượt 18 features = `6 SLA × 3 đại lượng`):**
   * Nhận 6 thông số SLA + 6 `*_roll_mean` + 6 `*_sustained_ratio`.
   * Nhờ có cửa sổ trượt ngay ở Model 1, khi xuất hiện **gai nhiễu 1 dòng (`TransientSpike`)**, `sustained_ratio = 0.20` giữ điểm EIF ở mức **`Trung bình (MEDIUM)`**, **không bao giờ nhảy báo động giả lên `Nghiêm trọng (CRITICAL)`**.
   * Phân loại trạng thái hiện tại thành **4 mức độ**: **`Bình thường (NORMAL)`**, **`Trung bình (MEDIUM)`**, **`Cao (HIGH)`**, **`Nghiêm trọng (CRITICAL)`**. Đạt **`Accuracy = 98.21%`, `F1 = 0.9801`, `ROC-AUC = 0.9879`**.
2. **Bước 2 — Công thức Dự báo Xu hướng 5 phút tới (`compute_5m_trend_forecast`):**
   * Sử dụng công thức động học chuỗi thời gian $\hat{S}_{t+5m} = \text{EMA}(S_t) + \alpha \cdot \text{Slope}(S_{t-W..t}) \cdot R_{\text{sustained}}$ để dự báo **4 mức độ tương lai (`NORMAL`, `MEDIUM`, `HIGH`, `CRITICAL`)** (đã loại bỏ hoàn toàn `model2_xgboost_forecaster.pkl` cũ).
3. **Bước 3 — Model 2 ([`ai/models/model2_rca_classifier.pkl`](ai/models/model2_rca_classifier.pkl) — XGBoost Multi-class RCA dùng 36 features Cửa sổ trượt = `6 SLA × 6 đại lượng`):**
   * Khi dịch vụ ở mức `Cao / Nghiêm trọng` và dự báo kéo dài, Model 2 dựa vào 36 đặc trưng cửa sổ trượt (thuần túy từ 6 thông số SLA gốc) để phân loại chính xác **5 lớp**: `Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan` (đạt **`Accuracy = 100%`, `Macro-F1 = 1.0000`, `CV-5 F1 = 0.9941`**).

### ✅ 3.3. Tự động Ứng phó Hướng Dịch vụ (Service-Centric Remediation — Đã bỏ kịch bản WAN)
* Đã loại bỏ hoàn toàn kịch bản WAN khỏi phần khắc phục dịch vụ để triệt tiêu xung đột hạ tầng dùng chung.
* Trong [`monitoring/aiops-dashboard/backend/remediation.py`](monitoring/aiops-dashboard/backend/remediation.py):
  * Mọi câu lệnh `iptables` đều chỉ định chính xác **`-d <Service_IP> -p tcp --dport <Service_Port>`** (ví dụ chặn `SshBruteforce` ở cổng `:22` không làm ảnh hưởng `Web Portal` ở cổng `:80` trên cùng máy chủ `192.168.10.10`).
  * Kiểm tra chống trùng lặp (`iptables -C`) trước khi chèn (`iptables -I`).
  * Hỗ trợ **Phê duyệt & Gỡ rule (`Rollback` bằng `iptables -D`) riêng cho từng dịch vụ** trên trang **`Tự động Ứng phó` (`#/remediation`)** mà không xóa nhầm rule đang bảo vệ dịch vụ khác.

### ✅ 3.4. Quản lý Dịch vụ Động (CRUD Services) & Bố cục Giao diện React
* **Trang `Giám sát Hiệu năng` (`#/overview`):**
  1. **Thứ nhất:** 4 thẻ KPI của dịch vụ đang chọn (Mức độ hiện tại Model 1 theo 4 mức · Dự báo 5 phút tới theo 4 mức · Chẩn đoán RCA & Bảo vệ `IP:Port`).
  2. **Thứ hai:** Thanh kích hoạt kịch bản (`Bình thường`, `Gai nhiễu thoáng qua (1 mẫu)`, `SYN Flood (Web :80)`, `HTTP Flood (Web/API/DB)`, `SSH Brute-force (:22)`, `Port Scan nội bộ (LAN)`).
  3. **Thứ ba:** **Lưới 6 Biểu đồ SLA LIVE 2s** (`Latency`, `Jitter`, `Packet Loss`, `Throughput`, `Response Time`, `Uptime`) kèm Dropdown chọn dịch vụ.
  4. **Thứ tư (Cuối trang):** Bảng danh sách trạng thái AI & 6 thông số SLA của toàn bộ các dịch vụ.
* **Trang `Dịch vụ Server` (`#/services`):**
  * Tích hợp biểu mẫu **Thêm dịch vụ mới (`POST /api/services`)**, **Sửa dịch vụ (`PUT /api/services/<id>`)** và **Xóa dịch vụ (`DELETE /api/services/<id>`)** đồng bộ trực tiếp lên **Cloud MongoDB Atlas (`AIOpsProject > Services`)**.
  * Ngay khi tạo dịch vụ mới, bộ thu thập lập tức đo 6 thông số SLA, tạo cửa sổ trượt và vẽ 6 biểu đồ sống cho dịch vụ đó.
