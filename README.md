# Hệ thống Giám sát, Phát hiện Bất thường và Tự động Ứng phó Sự cố Mạng dựa trên AIOps

> **Đồ án tốt nghiệp:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.
> **Điểm cốt lõi:** 100% dữ liệu đo thực tế từ hạ tầng mạng Containerlab (không giả lập số liệu), kết hợp **2 mô hình AI** + **Dự báo xu hướng 5 phút** + **Ứng phó khép kín bằng `iptables` thật** + **RBAC** + **Telegram ChatOps Leo thang đa tầng**.

---

## 1. Kiến trúc tổng quan

Hệ thống tách biệt hoàn toàn **Data Plane** (mạng doanh nghiệp gồm Trụ sở chính HQ, Chi nhánh Branch kết nối OSPF 2 đường WAN Primary/Backup, Internet, ứng dụng 3 tầng `Nginx → Flask API → PostgreSQL` và `OpenSSH`) với **Out-of-Band Management Plane (`172.20.20.0/24`)** để đảm bảo cụm giám sát và tự động ứng phó luôn sống sót khi Data Plane bị tấn công DDoS hoặc đứt đường truyền.

Chi tiết quy hoạch IP và sơ đồ thiết bị xem tại:
- [`docs/network-design.md`](docs/network-design.md)
- [`docs/topo.drawio`](docs/topo.drawio)

### Luồng xử lý AIOps khép kín (Closed-Loop)

```text
Containerlab (Data Plane + Kernel Accounting + Prometheus 2s)
   │
   ▼  6 SLA KPIs đo thật: latency, jitter, packet_loss, throughput, response_time, uptime
   │  (Cửa sổ trượt 5 bước = 36 đặc trưng chuỗi thời gian)
   ▼
[Model 1 — Extended Isolation Forest (18 features)]
   │  Phân mức 4 cấp hiện tại: NORMAL / MEDIUM / HIGH / CRITICAL
   ▼
[Dự báo xu hướng 5 phút — S_hat = EMA(S_t) + alpha * Slope * R_sustained]
   │  Tầng 0: Lọc dao động thoáng qua (TransientSpike) -> 0 báo giả, 0 luật iptables
   ▼  Policy Gate (Hiện tại HIGH/CRITICAL & Dự báo 5m >= ngưỡng)
[Model 2 — XGBoost Multi-class RCA (36 features)]
   │  Chẩn đoán 5 lớp: Normal, SynFlood, HttpFlood, SshBruteforce, PortScan
   │  (Độ tin cậy < 70% hoặc dịch vụ sập không có lưu lượng tấn công -> Unknown_Anomaly, không tự chặn)
   ▼
[Tự động ứng phó & ChatOps]
   ├── Truy vết IP nguồn động từ kernel (hỗ trợ Internet / Branch / Insider LAN / đa nguồn)
   ├── Chọn điểm chặn tối ưu: North–South tại Firewall (FORWARD) · East–West LAN tại Host đích (INPUT)
   ├── Chế độ Auto-Pilot hoặc Human-in-the-Loop (duyệt qua Web hoặc Telegram Inline Buttons)
   └── Leo thang đa tầng L1 (0s) → L2 (30s) → L3 Tự bảo vệ (60s) + Auto-Resolved + Lưu MongoDB Atlas
```

---

## 2. Kết quả Thực nghiệm 2 Mô hình AI (Dataset 100% Đo Thật)

Dataset thực nghiệm gồm **2.656 mẫu thô** và **2.188 mẫu cửa sổ trượt 5 bước** thu thập qua 3 chu kỳ độc lập trực tiếp trên Containerlab (`ai/dataset/`):

| Mô hình | Thuật toán & Đặc trưng | Hiệu năng trên tập Test thật |
|---|---|---|
| **Model 1 — Anomaly Detector** (`ai/models/model1_eif_detector.pkl`) | Extended Isolation Forest · 18 đặc trưng trượt · 4 ngưỡng hiệu chuẩn | `Accuracy = 98.40%`, `Precision = 96.53%`, `Recall = 99.40%`, `F1 = 0.9795`, `ROC-AUC = 0.9987`, `PR-AUC = 0.9979` |
| **Model 2 — Root Cause Analysis** (`ai/models/model2_rca_classifier.pkl`) | XGBoost Multi-class · 36 đặc trưng trượt · 5 lớp · ngưỡng tự tin `70%` | `Accuracy = 100.00%`, `Macro-F1 = 1.0000`, `5-Fold CV F1 = 0.9941` |

---

## 3. Tính năng chính của AIOps Command Center (`:5000`)

- **Giám sát thời gian thực theo từng dịch vụ (Service-Centric):** 6 thông số SLA, biểu đồ chuỗi thời gian, ma trận đặc trưng cửa sổ trượt 36 chiều, hỗ trợ CRUD thêm/sửa/xóa dịch vụ động.
- **Đăng nhập & Phân quyền RBAC (`ADMIN` / `VIEWER`):** Mật khẩu băm `PBKDF2-HMAC-SHA256`, Signed Token `HMAC-SHA256` thu hồi tức thời khi đổi mật khẩu / khóa / xóa tài khoản, chống brute-force đăng nhập, trang quản lý tài khoản `#/users`.
- **Telegram ChatOps & Leo thang đa tầng `L0→L3`:** Long Polling (`getUpdates`, không cần mở port inbound/SSL), nút bấm Inline (`Phê duyệt`, `Bỏ qua`, `Gỡ chặn`) đồng bộ 2 chiều với Web bằng `editMessageText`, hỗ trợ lệnh `/status`, `/pending`, `/id`, `/help`, tự gửi thông báo hồi phục `Auto-Resolved`.
- **Tích hợp Alertmanager Webhook (`POST /alerts`):** Tiếp nhận cảnh báo hạ tầng cứng (`HostDown`, `WANLinkDown`, `HighCpuUsage`...) từ Prometheus/Alertmanager vào MongoDB và Telegram (chỉ cho phép từ mạng OOB `172.20.20.0/24` / `localhost`).
- **Trang Cài đặt chung (`#/settings`):** Tinh chỉnh nóng thời gian leo thang `L1→L2→L3`, chế độ `Auto-Pilot`, ngưỡng dự báo 5 phút, cooldown, bộ lọc Telegram và tần suất làm mới Web mà không cần khởi động lại server.
- **Lưu trữ Cloud Database (MongoDB Atlas):** 6 collection (`Services`, `AlertRules`, `AIResults`, `Alerts`, `RemediationActions`, `Users`) kèm Audit Trail đầy đủ trường `actor`.

---

## 4. Cấu trúc Thư mục

```text
.
├── topo.yml                           # Khai báo topology Containerlab (13 container + LAN bridge)
├── config/                            # Cấu hình FRR OSPF, Firewall, Nginx
├── app/                               # Ứng dụng mẫu 3 tầng (Flask API + PostgreSQL init)
├── ai/
│   ├── collector/                     # Thu thập dataset thực nghiệm từ Containerlab
│   ├── dataset/                       # Dataset CSV 100% đo thật
│   ├── models/                        # Model 1 (EIF) & Model 2 (XGBoost RCA) đã huấn luyện
│   ├── train.py / evaluate.py         # Huấn luyện & kiểm thử pipeline 6 kịch bản
│   └── kaggle_train_*.py              # Script huấn luyện chuẩn trên Kaggle
├── monitoring/
│   ├── prometheus/                    # Cấu hình scrape 2s & 5 luật cảnh báo hạ tầng
│   ├── alertmanager/                  # Định tuyến webhook tới AIOps Backend
│   ├── grafana/                       # Provisioning datasource & dashboard
│   └── aiops-dashboard/
│       ├── backend/                   # app.py, telemetry.py, remediation.py, database.py, auth.py, notifier.py
│       └── frontend/                  # React + Vite (đã build sẵn trong dist/)
├── scripts/
│   ├── deploy.sh / destroy.sh         # Triển khai & dọn dẹp lab + Web Server
│   ├── verify.sh / test-failover.sh   # Kiểm thử tự động 40 tiêu chí & kiểm thử OSPF failover
│   └── scenarios/00..05_*.sh          # 6 kịch bản lưu lượng & tấn công thực tế
├── docs/                              # Thiết kế mạng (network-design.md, topo.drawio)
├── .env.example                       # Mẫu biến môi trường (KHÔNG commit .env thật)
└── HANDOFF.md                         # Tài liệu bàn giao chi tiết kỹ thuật
```

---

## 5. Hướng dẫn Triển khai & Kiểm thử

### Bước 1: Chuẩn bị môi trường
```bash
cp .env.example .env
# Chỉnh sửa .env: điền MONGO_URI, AUTH_SECRET (chuỗi ngẫu nhiên >= 32 ký tự)
# và tùy chọn TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ADMIN_IDS

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Bước 2: Triển khai Lab & Khởi động AIOps Command Center
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```
Kết quả đạt chuẩn: **`40 PASS / 0 FAIL`**.

### Bước 3: Kiểm thử Pipeline AI (6 kịch bản)
```bash
.venv/bin/python ai/evaluate.py
```

### Bước 4: Truy cập các giao diện Web
- **AIOps Command Center:** `http://localhost:5000` *(tài khoản mặc định: `admin / admin123` và `viewer / viewer123` — nên đổi sau khi đăng nhập)*
- **Grafana:** `http://localhost:3000` *(mặc định: `admin / admin`)*
- **Prometheus:** `http://localhost:9090`
- **Alertmanager:** `http://localhost:9093`

> **Lưu ý khi chạy trên Azure VM qua SSH:** Mở port forwarding cho các cổng `5000`, `3000`, `9090` (và `9093`) trong tab **Ports** của IDE hoặc dùng:
> ```bash
> ssh -L 5000:127.0.0.1:5000 -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 azureuser@<IP_VM>
> ```

### Bước 5: Chạy thử các kịch bản tấn công thực tế
```bash
bash scripts/scenarios/01_internet_syn_flood.sh 60    # SYN Flood từ Internet (chặn tại Firewall)
bash scripts/scenarios/02_insider_port_scan.sh 45     # Port Scan nội bộ LAN (chặn tại Service Server)
bash scripts/scenarios/03_ssh_bruteforce.sh 45        # SSH Brute-force (:22)
bash scripts/scenarios/04_server_http_flood.sh 45     # HTTP Flood đa tầng
bash scripts/scenarios/05_transient_spike.sh          # Dao động thoáng qua (bị lọc báo giả, 0 luật chặn)
```

---

## 6. Dọn dẹp Lab
```bash
sudo ./scripts/destroy.sh
```
