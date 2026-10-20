# HANDOFF – Dự án AIOps (đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 6. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-10-20. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Đề tài và yêu cầu

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

**Nội dung thực hành (5 yêu cầu của đề cương):**
1. Thiết kế, triển khai mô phỏng topo mạng doanh nghiệp trên môi trường Cloud / Containerlab.
2. Xây dựng pipeline thu thập số liệu thời gian thực bằng **Prometheus** và dashboard **Grafana**.
3. Xây dựng kịch bản mô phỏng các tình huống tấn công, sự cố để thu thập dữ liệu và gán nhãn.
4. Huấn luyện và triển khai mô hình AI phân tích dữ liệu mạng để phát hiện sớm bất thường và phân loại sự cố.
5. Xây dựng module tự động phản ứng và xử lý sự cố.

**Nội dung lý thuyết liên quan:** mô hình AIOps và kiến trúc tự động hóa khép kín; giao thức thu thập dữ liệu và giám sát thời gian thực; ảo hóa hạ tầng bằng Container và Network as Code; ML/DL cho phát hiện bất thường chuỗi thời gian và lưu lượng mạng.

---

## 2. Môi trường

| Mục | Giá trị |
|---|---|
| Nền tảng | Ubuntu Linux, Docker 29.x, Containerlab 0.81.x |
| Thư mục dự án | `/home/dash/Desktop/AIOpsProject` (hoặc `~/aiops-project` trên Azure VM) |
| Git | Đã khởi tạo Git repo, remote `origin/main` |

---

## 3. Trạng thái hiện tại

### Giai đoạn 1 – Topology: ✅ HOÀN THÀNH và đã kiểm chứng
Đã kiểm chứng đạt 26/26 PASS, OSPF WAN failover Primary (cost 10) ↔ Backup (cost 50) hội tụ ~1s.

### Giai đoạn 2 – Giám sát (Prometheus, Alertmanager, Grafana): ✅ HOÀN THÀNH & ĐÃ KIỂM CHỨNG (31/31 PASS)
1. **Prometheus (`172.20.20.51:9090`)**:
   - File cấu hình: `monitoring/prometheus/prometheus.yml` (scrape interval 5s qua mạng OOB `172.20.20.0/24`).
   - Alert rules: `monitoring/prometheus/rules/network_alerts.yml` (HostDown, WANLinkDown, HighPacketRate, HighPacketDropRate, HighCpuUsage).
2. **Alertmanager (`172.20.20.52:9093`)**:
   - File cấu hình: `monitoring/alertmanager/alertmanager.yml` (đã khai báo route webhook tới Auto-Responder `172.20.20.55:5000/alerts`).
3. **Grafana (`172.20.20.53:3000`)**:
   - Datasource tự động: `monitoring/grafana/provisioning/datasources/prometheus.yml`.
   - Dashboard provider: `monitoring/grafana/provisioning/dashboards/dashboards.yml`.
   - Dashboard thời gian thực hoàn chỉnh: `monitoring/grafana/dashboards/network-overview.json` (giám sát node online, WAN1 vs WAN2 traffic, firewall flow, rớt gói, CPU load, firing alerts).
4. **Node Exporter trên các thiết bị mạng**:
   - Tích hợp tự động cài đặt và khởi chạy `node_exporter` (port 9100) trên `core-router`, `branch-router`, `firewall`, `service-server`, `client-pc`, `branch-client`, `internet-attacker`.
5. **Cập nhật scripts:**
   - `scripts/deploy.sh`: Thêm phân quyền `chmod -R 755 monitoring/`.
   - `scripts/verify.sh`: Bổ sung kiểm tra sức khỏe Prometheus API, Alertmanager, Grafana và số lượng active scrape targets.

### Giai đoạn 3, 4, 5: ⏳ CHƯA BẮT ĐẦU

### ⚠ Quy trình khởi động Lab
Containerlab không tự động lưu trạng thái sau khi máy reboot. Mỗi lần khởi động chạy:
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```

---

## 4. Cấu trúc thư mục

```
AIOpsProject/
├── topo.yml                         # Topology Containerlab tích hợp cụm AIOps
├── config/                          # Cấu hình FRR cho các Router
│   ├── core-frr.conf
│   ├── branch-frr.conf
│   ├── daemons
│   └── vtysh.conf
├── monitoring/                      # Toàn bộ cấu hình Giám sát Giai đoạn 2
│   ├── prometheus/
│   │   ├── prometheus.yml          # Scrape target OOB, alertmanager target
│   │   └── rules/
│   │       └── network_alerts.yml  # Luật cảnh báo mạng & thiết bị
│   ├── alertmanager/
│   │   └── alertmanager.yml        # Định tuyến webhook tới auto-responder
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/        # Tự động nạp Prometheus datasource
│       │   └── dashboards/         # Tự động nạp dashboard
│       └── dashboards/
│           └── network-overview.json # Dashboard giám sát trực quan hóa
├── scripts/
│   ├── deploy.sh                    # Tạo bridge lan-switch, cấp quyền và deploy
│   ├── destroy.sh                   # Huỷ lab và xoá bridge
│   ├── verify.sh                    # Kiểm tra end-to-end (mạng + giám sát)
│   └── test-failover.sh             # Demo WAN failover OSPF
├── docs/network-design.md           # Thiết kế mạng, sơ đồ Mermaid, bảng IP
└── HANDOFF.md                       # File bàn giao dự án
```

---

## 5. Topology hiện tại

```
internet-attacker ── core-router ══(WAN1 cost10 / WAN2 cost50, OSPF)══ branch-router ── branch-client
                         │
                     firewall ── lan-switch ──┬── service-server (Nginx, mục tiêu)
                                              └── client-pc (Người dùng LAN / attacker nội bộ)

[Mạng quản lý OOB 172.20.20.0/24 (eth0)]:
prometheus (.51) ── alertmanager (.52) ── grafana (.53) ──> scrape các node (.11, .12, .13, .21, .23, .31, .41)
```

---

## 6. VIỆC CẦN LÀM TIẾP: Giai đoạn 3 (Kịch bản sự cố & Thu thập Dataset)

1. **Xây dựng kịch bản mô phỏng sự cố (scripts mô phỏng có gán nhãn thời gian):**
   - **Kịch bản 1 - Đứt WAN1 (Link Failure):** Đưa `eth2` của router down/up, quan sát OSPF failover sang WAN2.
   - **Kịch bản 2 - SYN Flood DDoS:** Sử dụng `hping3` từ `internet-attacker` bắn vào `192.168.10.10:80`.
   - **Kịch bản 3 - Quét cổng do thám (Port Scanning):** Sử dụng `nmap` từ `client-pc` hoặc `branch-client`.
   - **Kịch bản 4 - Nghẽn băng thông (Bandwidth Saturation):** Sử dụng `iperf3` từ `branch-client` qua đường WAN.
   - **Kịch bản 5 - Suy hao & Độ trễ mạng (Packet Loss & Latency Injection):** Dùng `tc qdisc / netem` chèn jitter, delay, loss trên cổng router/firewall.
2. **Bộ thu thập số liệu (Metric Exporter / Data Collector Script):**
   - Viết script Python truy vấn Prometheus API (`/api/v1/query_range`) để xuất dữ liệu timeseries ra định dạng CSV/Parquet cùng với nhãn nhị phân (`is_anomaly`) và nhãn phân loại đa lớp (`attack_type`).
   - Đan xen các khoảng thời gian chạy lưu lượng bình thường (traffic sạch) và lưu lượng tấn công để tạo tập dữ liệu huấn luyện cân bằng.
