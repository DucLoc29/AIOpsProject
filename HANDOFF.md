# HANDOFF – Dự án AIOps (Đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 5 → 6. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-10-20. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Đề tài và yêu cầu

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

**5 yêu cầu thực hành của đề cương:**
1. Thiết kế, triển khai mô phỏng topology mạng doanh nghiệp trên môi trường Containerlab / Linux containers.
2. Xây dựng pipeline thu thập số liệu thời gian thực bằng **Prometheus** và dashboard **Grafana**.
3. Xây dựng kịch bản mô phỏng các tình huống tấn công, sự cố (suy giảm đường truyền, thiết bị, ứng dụng) để thu thập dữ liệu và gán nhãn.
4. Huấn luyện và triển khai mô hình AI phân tích dữ liệu mạng để phát hiện sớm bất thường (*Isolation Forest / LSTM-AE*) và phân loại sự cố (*Random Forest / XGBoost*).
5. Xây dựng module tự động phản ứng và xử lý sự cố khép kín (*Closed-loop Auto-remediation*).

---

## 2. Môi trường & Triển khai

| Mục | Giá trị |
|---|---|
| Nền tảng | Ubuntu Linux, Docker 29.x, Containerlab 0.81.x |
| Thư mục dự án | `/home/dash/Desktop/AIOpsProject` (hoặc `~/aiops-project` trên Azure VM) |
| Git Repo | `https://github.com/DucLoc29/AIOpsProject.git` (nhánh `main`) |
| Giao diện quản lý | Grafana: `http://localhost:3000` (admin/admin) · Prometheus: `http://localhost:9090` · Alertmanager: `http://localhost:9093` |

### ⚠ Quy trình khởi động Lab
Containerlab không tự động lưu trạng thái sau khi máy reboot. Mỗi lần khởi động chạy:
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```

---

## 3. Trạng thái hiện tại: ĐÃ LÀM ĐƯỢC

### ✅ Giai đoạn 1 – Topology Mạng Doanh Nghiệp: HOÀN THÀNH & KIỂM CHỨNG
* Mô phỏng mạng doanh nghiệp hoàn chỉnh: Core Router, Branch Router, Firewall, LAN Switch, Web Server, Client LAN, Branch Client, Internet Attacker.
* OSPF Area 0 trên 2 đường WAN point-to-point: WAN1 Primary (cost 10) và WAN2 Backup (cost 50).
* Định tuyến động OSPF hội tụ cực nhanh (~1s) khi failover. Đạt 26/26 PASS ở GĐ1.

### ✅ Giai đoạn 2 – Pipeline Giám sát Real-time: HOÀN THÀNH & KIỂM CHỨNG (31/31 PASS)
1. **Phân tách Cụm Giám sát độc lập qua Mạng OOB (`172.20.20.0/24`):**
   * Gỡ bỏ node tạm thời `aiops-server` khỏi LAN.
   * Tách thành 3 microservices độc lập: `prometheus` (.51:9090), `alertmanager` (.52:9093), `grafana` (.53:3000) chỉ nối qua `eth0`.
   * **Lý do kỹ thuật cốt lõi:** Khi Data Plane bị nghẽn hoặc tấn công DoS, kênh OOB vẫn sống sót 100% để thu thập dữ liệu và gửi lệnh phản ứng.
2. **Triển khai Node Exporter diện rộng:**
   * Tự động cài đặt và chạy nền `node_exporter` (port 9100) trên toàn bộ 7 node mạng (`core-router`, `branch-router`, `firewall`, `service-server`, `client-pc`, `branch-client`, `internet-attacker`).
   * Scrape interval 5s cung cấp dữ liệu chuỗi thời gian độ phân giải cao phục vụ AI.
3. **Cấu hình Prometheus & Alertmanager:**
   * `monitoring/prometheus/prometheus.yml`: Scrape cấu hình chuẩn kèm nhãn (`zone`, `device_type`, `instance`). Đã xác thực bằng `promtool`.
   * `monitoring/prometheus/rules/network_alerts.yml`: 5 rules cảnh báo (`HostDown`, `WANLinkDown`, `HighPacketRate`, `HighPacketDropRate`, `HighCpuUsage`).
   * `monitoring/alertmanager/alertmanager.yml`: Cấu hình route webhook sẵn sàng bắn về Auto-Responder (`http://172.20.20.55:5000/alerts`).
4. **Grafana Provisioning & Real-time Dashboard:**
   * Nạp tự động Datasource và Dashboard JSON (`monitoring/grafana/dashboards/network-overview.json`).
   * Trực quan hóa: Trạng thái node online, so sánh lưu lượng WAN1 (cost 10) vs WAN2 (cost 50), lưu lượng Firewall in/out, lưu lượng web server, tỷ lệ rớt gói (drops/s), tải CPU.
5. **Cải tiến Scripts & Sửa lỗi vận hành:**
   * `scripts/deploy.sh`: Tự động dò tìm đường dẫn `containerlab` trong mọi trường hợp (`sudo`, `PATH`, `~/.local/bin/`); cấp quyền `chmod -R 755 monitoring/`.
   * `scripts/destroy.sh`: Tự động tìm `containerlab` và dọn dẹp sạch sẽ bridge `lan-switch`.
   * `scripts/verify.sh`: Tự nhận diện `docker` không cần sudo, kiểm tra sức khỏe Prometheus, Alertmanager, Grafana và xác thực toàn bộ scrape targets.
   * **Kết quả kiểm chứng mới nhất:** Đạt **31/31 PASS** (100% tests thành công).

### ✅ Giai đoạn 3 – Bước 1: Dịch vụ doanh nghiệp (đã cấu hình, chờ xác nhận verify.sh)
* **Kiến trúc 3 tầng trong LAN:** `service-server` (Nginx :80 reverse proxy `/api/` + SSH :22, user `opsadmin`/`Corp@2026`) → `app-server` (Flask API :5000, `.20`) → `db-server` (PostgreSQL :5432, `.21`, db `corp`).
* `nginx-exporter` (172.20.20.54:9113) đọc `/stub_status`; Prometheus có thêm job `nginx` và `api` (`app-server:5000/metrics`).
* Mã nguồn: `app/nginx.conf`, `app/html/`, `app/api/app.py`, `app/db/init.sql`.
* Quy ước: dịch vụ nhẹ sau này (DNS...) gộp vào `service-server`, không tạo node mới.
* DNS của Docker host có lúc lỗi nên các node cài gói đặt `nameserver 8.8.8.8` trước `apk add`. `zebra` của core-router thỉnh thoảng crash khi khởi động: chạy lại deploy.
* **Lưu ý cho kịch bản (Bước 2):** cần cài `iperf3`, `tc` (iproute2-tc), tool HTTP flood; cẩn thận conntrack (kernel host dùng chung).

---

## 4. Thiết kế & Cơ sở lý luận đã thống nhất

### 4.1. Ba hướng tấn công và điểm chặn tự động
* **Hướng 1 - Internet Attacker (`203.0.113.10`):** Bắc - Nam (North - South) qua Core Router và Firewall $\rightarrow$ Chặn tại **Firewall** (`iptables`).
* **Hướng 2 - Chi nhánh Attacker (`172.16.1.10`):** Máy chi nhánh nhiễm mã độc tấn công qua WAN $\rightarrow$ Chặn tại **Firewall** (`iptables`).
* **Hướng 3 - Nội bộ Attacker (`192.168.10.30`):** Insider threat cùng dải LAN với server, **không đi qua Firewall** $\rightarrow$ Chặn trực tiếp tại **Service Server** (`iptables`).

### 4.2. Bản chất suy giảm WAN & Giá trị cốt lõi của AIOps
* **Hạn chế OSPF truyền thống:** Chỉ chuyển đường khi đứt link vật lý (Hard Failure). Khi WAN1 bị "suy giảm mềm" (Soft/Gray Failure: rớt gói 25%, nghẽn băng thông 98%, latency cao), OSPF vẫn giữ cost 10 và **không tự chuyển sang WAN2**, làm gián đoạn người dùng.
* **Cơ chế AIOps:** Prometheus & AI phát hiện chất lượng WAN1 suy giảm $\rightarrow$ Auto-Responder tự động đổi OSPF cost WAN1 từ 10 lên 100 bằng lệnh `vtysh` $\rightarrow$ OSPF lập tức lái lưu lượng sang WAN2 (cost 50). Khi WAN1 hết nghẽn, AIOps hạ cost về 10.

### 4.3. Ma trận suy giảm hiệu năng toàn diện 3 tầng
1. **Tầng 1 - Đường truyền (Network Link):** Nghẽn băng thông WAN1 (`iperf3`); Mất gói và Trễ cao (`tc qdisc netem delay 200ms loss 20%`).
2. **Tầng 2 - Thiết bị mạng (Network Device):** Tràn bảng session/conntrack Firewall (`hping3 --rand-source`).
3. **Tầng 3 - Ứng dụng & Máy chủ (Server & Application):** Web Server HTTP Flood / Quá tải kết nối (`ab` / `curl` loop), xuất hiện mã lỗi HTTP 502/504.

---

## 5. Cấu trúc thư mục dự án

```
AIOpsProject/
├── topo.yml                         # Topology Containerlab tích hợp cụm AIOps
├── config/                          # Cấu hình FRR cho các Router
│   ├── core-frr.conf
│   ├── branch-frr.conf
│   ├── daemons
│   └── vtysh.conf
├── monitoring/                      # Pipeline Giám sát (Prometheus & Grafana)
│   ├── prometheus/
│   │   ├── prometheus.yml          # Cấu hình scrape OOB (5s interval)
│   │   └── rules/
│   │       └── network_alerts.yml  # 5 luật cảnh báo hạ tầng & mạng
│   ├── alertmanager/
│   │   └── alertmanager.yml        # Định tuyến webhook tới Auto-Responder
│   └── grafana/
│       ├── provisioning/           # Nạp tự động Datasource & Dashboard
│       └── dashboards/
│           └── network-overview.json # Dashboard trực quan hóa AIOps
├── app/                             # [SẼ LÀM Ở GĐ3] Web Portal & Nginx config cho service-server
│   ├── html/                       # Giao diện Corporate Web Portal
│   └── nginx.conf                  # Nginx config kích hoạt /stub_status
├── scripts/
│   ├── deploy.sh                    # Khởi chạy lab & monitoring
│   ├── destroy.sh                   # Hủy lab & bridge
│   ├── verify.sh                    # Kiểm tra end-to-end (31/31 PASS)
│   ├── test-failover.sh             # Demo WAN failover OSPF
│   └── scenarios/                   # [ĐÃ HOÀN THÀNH Ở GĐ3] Bộ 7 script kịch bản
│       ├── 00_normal_traffic.sh     # Sinh lưu lượng bình thường (baseline)
│       ├── 01_wan_congestion.sh     # Nghẽn băng thông WAN1 (iperf3 từ chi nhánh)
│       ├── 02_wan_loss_latency.sh   # Chèn suy hao trễ/mất gói tc netem trên WAN1
│       ├── 03_internet_syn_flood.sh # Tấn công DoS SYN Flood từ Internet
│       ├── 04_insider_port_scan.sh  # Quét cổng trinh sát nmap từ Insider LAN
│       ├── 05_ssh_bruteforce.sh     # Dò quét mật khẩu SSH từ Internet
│       └── 06_server_http_flood.sh  # Quá tải ứng dụng HTTP/API
├── collector/                       # [SẼ LÀM Ở BƯỚC 3 - GĐ3] Pipeline thu thập & gán nhãn dataset
│   └── generate_dataset.py          # Orchestrator chạy kịch bản, query API, xuất CSV
├── dataset/                         # [SẼ LÀM Ở BƯỚC 3 - GĐ3] Chứa file CSV dataset đã gán nhãn
├── ai/                              # [GIAI ĐOẠN 4] Pipeline huấn luyện Machine Learning
│   ├── train.py                     # Huấn luyện Isolation Forest & Random Forest/XGBoost
│   ├── evaluate.py                  # Đánh giá Accuracy, F1-Score, Confusion Matrix
│   └── models/                      # Nơi lưu các mô hình đã huấn luyện (.pkl / .joblib)
├── aiops-dashboard/                 # [GIAI ĐOẠN 5] Trung tâm chỉ huy AIOps (Console)
│   ├── backend/                     # Python FastAPI/Flask (Webhook receiver + AI inference + Remediation)
│   └── frontend/                    # Web Console (Real-time alert feed, confidence, manual override)
└── docs/network-design.md           # Thiết kế mạng, sơ đồ Mermaid, bảng IP
```

---

## 6. KẾ HOẠCH HÀNH ĐỘNG TIẾP THEO: GIAI ĐOẠN 3

### ✅ Bước 1: Nâng cấp Dịch vụ Doanh nghiệp trên `service-server` (ĐÃ HOÀN THÀNH)
* Đã triển khai kiến trúc 3 tầng: Nginx Web + Flask API + PostgreSQL + OpenSSH.
* Đã cấu hình Nginx reverse proxy `/api/` và bật `/stub_status`.
* Đã cấu hình node `nginx-exporter` (172.20.20.54:9113) và Prometheus scrape job `nginx`, `api`.

### ✅ Bước 2: Xây dựng Bộ Script Kịch bản Sự cố & Tấn công (`scripts/scenarios/`) (ĐÃ HOÀN THÀNH)
* Đã viết và kiểm thử độc lập 7 kịch bản (nhận tham số thời lượng `DURATION`, tự cleanup khi thoát):
  * `00_normal_traffic.sh`: Sinh lưu lượng hợp lệ từ `client-pc` và `branch-client`.
  * `01_wan_congestion.sh`: Đẩy tải băng thông kịch trần qua WAN1 bằng `iperf3` từ chi nhánh.
  * `02_wan_loss_latency.sh`: Chèn delay 200ms và loss 20% trên WAN1 bằng `tc netem`.
  * `03_internet_syn_flood.sh`: SYN flood tốc độ cao từ `internet-attacker` tới port 80.
  * `04_insider_port_scan.sh`: Quét cổng do thám từ máy nội bộ `client-pc` (không qua FW).
  * `05_ssh_bruteforce.sh`: Dò quét mật khẩu SSH liên tục từ `internet-attacker`.
  * `06_server_http_flood.sh`: Bắn tải HTTP/API đồng thời từ Internet và Chi nhánh.

### Bước 3: Xây dựng Pipeline Thu thập & Gán nhãn Dataset (`collector/`)
* Xây dựng script Python `collector/generate_dataset.py`:
  1. Chỉ huy chạy đan xen có kiểm soát: `Traffic sạch (2 phút)` $\rightarrow$ `Sự cố 1 (1 phút)` $\rightarrow$ `Traffic sạch` $\rightarrow$ `Sự cố 2` $\rightarrow$ ...
  2. Tự động ghi lại chính xác mốc thời gian bắt đầu và kết thúc của từng sự cố.
  3. Gọi REST API của Prometheus (`/api/v1/query_range`) để trích xuất toàn bộ metrics mạng và hệ thống.
  4. Khớp nhãn nhị phân (`is_anomaly`: 0 hoặc 1) và nhãn đa lớp (`label`: `Normal`, `WanCongestion`, `WanLossLatency`, `SynFlood`, `PortScan`, `SshBruteforce`, `HttpFlood`).
  5. Xuất tập dữ liệu ra file `dataset/aiops_network_dataset.csv` làm đầu vào sẵn sàng cho Giai đoạn 4.

---

## 7. ĐỊNH HƯỚNG THIẾT KẾ GIAI ĐOẠN 4 & 5 (ĐÃ THỐNG NHẤT)

### 7.1. Giai đoạn 4: Huấn luyện & Xuất mô hình AI (`ai/`)
* **Dữ liệu đầu vào:** `dataset/aiops_network_dataset.csv`.
* **Mô hình 1 - Phát hiện bất thường sớm (Unsupervised):** *Isolation Forest* / *LSTM-AE* $\rightarrow$ xuất ra `ai/models/anomaly_detector.pkl`.
* **Mô hình 2 - Phân loại nguyên nhân sự cố (Supervised Multi-class):** *Random Forest* / *XGBoost* $\rightarrow$ xuất ra `ai/models/fault_classifier.pkl` (7 nhãn tương ứng 7 kịch bản).
* Đánh giá mô hình: Độ chính xác (Accuracy), F1-Score, Confusion Matrix phục vụ báo cáo đồ án.

### 7.2. Giai đoạn 5: Trung tâm chỉ huy AIOps Console & Phản ứng khép kín (`aiops-dashboard/`)
* **Lý do cần Web Console riêng:** Grafana chỉ hiển thị biểu đồ time-series, không thể hiển thị kết quả suy luận AI theo thời gian thực (độ tin cậy confidence %, nguyên nhân gốc RCA), không có bảng theo dõi lịch sử can thiệp (action audit log) và không có nút bấm tương tác phê duyệt.
* **Backend (`aiops-dashboard/backend/` - Python FastAPI/Flask):**
  * Nhận Webhook cảnh báo từ Alertmanager (`:9093`).
  * Nạp các file mô hình `.pkl` từ `ai/models/` để thực hiện suy luận (Inference).
  * Bộ thực thi tự động (Remediation Engine): gửi lệnh can thiệp qua OOB (`vtysh` đổi OSPF cost, `iptables` drop IP, restart service).
  * Hỗ trợ 2 chế độ: **Auto-pilot** (tự động 100%) và **Human-in-the-loop** (chờ kỹ sư duyệt trên Web).
* **Frontend (`aiops-dashboard/frontend/`):**
  * Giao diện Web Console hiện đại (Dark-mode).
  * Bảng tin cảnh báo thời gian thực (Real-time Live Alert Feed).
  * Đồ thị phân tích đặc trưng lỗi & nút bấm can thiệp/phê duyệt khắc phục.


