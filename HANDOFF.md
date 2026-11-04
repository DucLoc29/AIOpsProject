# HANDOFF – Dự án AIOps (Đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 5 → 6 → 7. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-11-04. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Đề tài và yêu cầu

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

**5 yêu cầu thực hành của đề cương:**
1. Thiết kế, triển khai mô phỏng topology mạng doanh nghiệp trên môi trường Containerlab / Linux containers.
2. Xây dựng pipeline thu thập số liệu thời gian thực bằng **Prometheus** và dashboard **Grafana**.
3. Xây dựng kịch bản mô phỏng các tình huống tấn công, sự cố (suy giảm đường truyền, thiết bị, ứng dụng) để thu thập dữ liệu và gán nhãn.
4. Huấn luyện và triển khai bộ 3 mô hình AI độc lập để phát hiện bất thường tức thời (**Extended Isolation Forest**), dự báo suy giảm chuỗi thời gian 5 phút (**XGBoost Forecaster**) và phân loại nguyên nhân gốc rễ (**XGBoost Multi-class RCA Classifier**).
5. Xây dựng Trung tâm Chỉ huy AIOps Web Console & module tự động phản ứng xử lý sự cố khép kín (*Closed-loop Auto-remediation*).

---

## 2. Môi trường & Quy trình Triển khai (Local VM $\rightarrow$ Git $\rightarrow$ Azure VM)

| Mục | Giá trị |
|---|---|
| Nền tảng | Ubuntu Linux VM (Local & Azure VM đồng nhất hệ điều hành), Docker 29.x, Containerlab 0.81.x |
| Thư mục dự án | `/home/dash/Desktop/AIOpsProject` (Local VM) $\rightarrow$ `git push` $\rightarrow$ `~/aiops-project` (Azure VM) |
| Git Repo | `https://github.com/DucLoc29/AIOpsProject.git` (nhánh `main`) |
| Giao diện quản lý | **AIOps Command Center**: `http://localhost:5000` · **Grafana**: `http://localhost:3000` (admin/admin) · **Prometheus**: `http://localhost:9090` · **Alertmanager**: `http://localhost:9093` |

### ⚠ Quy trình khởi động Lab & AIOps Dashboard
Containerlab không tự động lưu trạng thái sau khi máy reboot. Mỗi lần khởi động chạy:
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
python3 monitoring/aiops-dashboard/app.py
```

---

## 3. Trạng thái hiện tại: CHI TIẾT NHỮNG GÌ ĐÃ LÀM ĐƯỢC

### ✅ Giai đoạn 1 – Topology Mạng Doanh Nghiệp: HOÀN THÀNH & KIỂM CHỨNG
* Mô phỏng mạng doanh nghiệp hoàn chỉnh: Core Router, Branch Router, Firewall, LAN Switch, Service Server (Web/SSH), App Server (Flask API), DB Server (PostgreSQL), Client PC (LAN), Branch Client, Internet Attacker.
* OSPF Area 0 trên 2 đường WAN point-to-point: **WAN1 Primary (cost 10)** và **WAN2 Backup (cost 50)**.
* Định tuyến động OSPF hội tụ cực nhanh (~1s) khi failover. Đạt 26/26 PASS ở GĐ1.

### ✅ Giai đoạn 2 – Pipeline Giám sát Real-time qua Mạng OOB: HOÀN THÀNH (31/31 PASS)
1. **Phân tách Cụm Giám sát & AIOps độc lập qua Mạng OOB (`172.20.20.0/24`):**
   * Tách cụm giám sát ra mạng quản lý Out-of-Band (`eth0`): `prometheus` (`.51:9090`), `alertmanager` (`.52:9093`), `grafana` (`.53:3000`), `nginx-exporter` (`.54:9113`), và quy hoạch `.55:5000` cho **AIOps Command Center (`monitoring/aiops-dashboard/`)**.
   * **Lý do kỹ thuật cốt lõi:** Khi Data Plane (`eth1`, `eth2`) bị nghẽn hoặc tấn công DoS, kênh OOB (`eth0`) vẫn sống sót 100% để thu thập dữ liệu và gửi lệnh phản ứng.
2. **Triển khai Exporters diện rộng (Scrape interval 5s):**
   * `node_exporter` (:9100) trên toàn bộ các node mạng + `nginx-exporter` (:9113) + `/metrics` trên `app-server` (:5000).
3. **Cấu hình Prometheus, Alertmanager & Grafana:**
   * `monitoring/prometheus/prometheus.yml` & `monitoring/prometheus/rules/network_alerts.yml` (5 luật cảnh báo).
   * `monitoring/alertmanager/alertmanager.yml`: Định tuyến webhook cảnh báo tới `http://172.20.20.55:5000/alerts`.
   * `monitoring/grafana/dashboards/network-overview.json`: Dashboard trực quan hóa băng thông WAN1 vs WAN2, Firewall, Web Server, CPU.

### ✅ Giai đoạn 3 – Dịch vụ Doanh nghiệp 3 Tầng, 7 Kịch bản Sự cố & Unified Dataset: HOÀN THÀNH (37/37 PASS)
* **Kiến trúc 3 tầng trong LAN (`app/`):** `service-server` (`192.168.10.10`, Nginx :80 reverse proxy `/api/` + SSH :22) $\rightarrow$ `app-server` (`192.168.10.20`, Flask API :5000) $\rightarrow$ `db-server` (`192.168.10.21`, PostgreSQL :5432, db `corp`).
* **Bộ 7 kịch bản (`scripts/scenarios/`):**
  * `00_normal_traffic.sh`, `01_wan_congestion.sh`, `02_wan_loss_latency.sh` (đã nâng cấp `tc netem` 2 chiều trên WAN1), `03_internet_syn_flood.sh`, `04_insider_port_scan.sh`, `05_ssh_bruteforce.sh`, `06_server_http_flood.sh`.
* **Pipeline thu thập & Hợp nhất 1 Bộ Dataset Duy nhất (`ai/collector/` & `ai/dataset/`):**
  * `ai/collector/generate_dataset.py`: Chạy đan xen 2 chu kỳ kịch bản đầy đủ, xuất chuỗi thời gian gốc `ai/dataset/raw_prometheus_timeseries.csv` (**713 mẫu × 46 cột**).
  * `ai/collector/prepare_two_stage_datasets.py`: Tạo ra **1 file Dataset Hợp nhất duy nhất (`ai/dataset/aiops_network_dataset.csv` - 700 mẫu × 117 cột)** dùng chung cho cả 3 Model (chỉ cần upload 1 file lên Kaggle, mỗi Model tự động chọn tập đặc trưng và nhãn mục tiêu của riêng mình):
    1. **2 cột định danh thời gian:** `timestamp`, `datetime`.
    2. **42 cột đặc trưng tức thời đa tầng** (WAN1/WAN2, Firewall, LAN/TCP Stack, Nginx/Flask API/PostgreSQL — trong đó có 14 chỉ số KPI cốt lõi `KEY_TIMESERIES_KPIS`).
    3. **70 cột đặc trưng cửa sổ trượt (Sliding Window):** Tính trên cửa sổ 5 bước cho 14 KPI cốt lõi (`*_roll_mean`, `*_roll_std`, `*_delta1`, `*_trend_slope`, `*_sustained_ratio`).
    4. **3 cột nhãn mục tiêu (Targets):** `current_degraded` (`0/1` cho Model 1), `degraded_forecast_5m` (`0/1` cho Model 2), `label` (`7 lớp` cho Model 3).

### ✅ Giai đoạn 4 – Huấn luyện 3 Mô hình AI Độc lập & 3 Kaggle Notebooks (`ai/`): HOÀN THÀNH
* **Kiến trúc 3 Model độc lập (Three-Stage Cascaded Pipeline) + Separation of Concerns:**
  1. **Model 1 (`ai/models/model1_eif_detector.pkl`) — Extended Isolation Forest (EIF):**
     * Tự động chọn **14 đặc trưng KPI cốt lõi tức thời** $\rightarrow$ Target: `current_degraded` (`0/1`).
     * Sử dụng siêu phẳng cắt nghiêng ngẫu nhiên ($\mathbf{n} \cdot \mathbf{x} \le p$), đạt **`F1 = 0.9848`, `Accuracy = 98.29%`** (vượt trội hơn Standard Isolation Forest `F1 = 0.9652`).
  2. **Model 2 (`ai/models/model2_xgboost_forecaster.pkl`) — XGBoost 5m Time-Series Forecaster:**
     * Tự động chọn **84 đặc trưng cửa sổ trượt** (`14 tức thời + 70 cột động học`) $\rightarrow$ Target: `degraded_forecast_5m` (`0/1`).
     * Đạt **`Recall = 95.96%`, `F1 = 0.9179`, `ROC-AUC = 0.9672`**. Khi gặp gai nhiễu thoáng qua (`TransientSpike`), Model 2 dự báo xác suất kéo dài chỉ `2.58%` (`< 60%`), giúp chặn đứng báo động giả.
  3. **Hệ thống Điều phối (System Policy Engine):**
     * Tách biệt hoàn toàn khỏi Model: Chỉ khi `Model 1 == 1` **VÀ** `Model 2 >= Ngưỡng Hệ thống (mặc định 60%)` mới kích hoạt Model 3.
  4. **Model 3 (`ai/models/model3_rca_classifier.pkl`) — XGBoost Multi-class RCA Classifier (`XGBoost_RCA`):**
     * Tự động chọn **42 đặc trưng đa tầng tức thời** $\rightarrow$ Target: `label` (`Normal`, `WanCongestion`, `WanLossLatency`, `SynFlood`, `PortScan`, `SshBruteforce`, `HttpFlood`).
     * Sử dụng **XGBoost (`XGBClassifier`)**, đạt **`Accuracy = 100%`, `Macro-F1 = 1.0000`, `5-Fold CV = 0.9972`**.
* **Bộ 3 Kaggle Notebooks riêng biệt (`ai/reports/`):**
  * `ai/reports/Kaggle_Model1_EIF_Instant_Detector.ipynb`
  * `ai/reports/Kaggle_Model2_XGBoost_5m_Forecaster.ipynb`
  * `ai/reports/Kaggle_Model3_RCA_Classifier.ipynb`

### ✅ Giai đoạn 5 (Phần 1) – Chuẩn hóa Cấu trúc Thư mục & Giao diện Cơ bản AIOps Command Center (`monitoring/aiops-dashboard/`): ĐÃ KHỞI TẠO
* Đã gom gọn toàn bộ dự án thành **5 trụ cột thư mục** (`config/`, `app/`, `monitoring/`, `ai/`, `scripts/` & `docs/`).
* Đã xây dựng phiên bản cơ bản của **Web Trung tâm Chỉ huy AIOps (`monitoring/aiops-dashboard/`)** chạy tại `http://localhost:5000`:
  * Nạp trực tiếp 3 file model `.pkl` thật và dataset `ai/dataset/aiops_network_dataset.csv`.
  * Hiển thị trực quan 4 khối Pipeline (`Model 1 EIF` $\rightarrow$ `Model 2 XGBoost 5m` $\rightarrow$ `System Policy Gate` $\rightarrow$ `Model 3 XGBoost RCA`), bảng 42 chỉ số mạng, biểu đồ xác suất 7 lớp và bảng Nhật ký Tự động Ứng phó (`Audit Log`).
  * Hỗ trợ thanh chọn 8 kịch bản mô phỏng tức thì, thanh trượt chỉnh ngưỡng dự báo (`30%–90%`), công tắc `Auto-Pilot` vs `Human-in-the-Loop`, và nút `Rollback`.

---

## 4. Thiết kế & Cơ sở lý luận đã thống nhất

### 4.1. Ba hướng tấn công và điểm chặn tự động
* **Hướng 1 - Internet Attacker (`203.0.113.10`):** Bắc - Nam (North - South) qua Core Router và Firewall $\rightarrow$ Chặn tại **Firewall** (`iptables`).
* **Hướng 2 - Chi nhánh Attacker (`172.16.1.10`):** Máy chi nhánh nhiễm mã độc tấn công qua WAN $\rightarrow$ Chặn tại **Firewall** (`iptables`).
* **Hướng 3 - Nội bộ Attacker (`192.168.10.30`):** Insider threat cùng dải LAN với server, **không đi qua Firewall** $\rightarrow$ Chặn trực tiếp tại **Service Server** (`iptables`).

### 4.2. Bản chất suy giảm WAN & Giá trị cốt lõi của AIOps
* **Hạn chế OSPF truyền thống:** Chỉ chuyển đường khi đứt link vật lý (Hard Failure). Khi WAN1 bị "suy giảm mềm" (Soft/Gray Failure: rớt gói 20%, nghẽn băng thông 98%, delay 200ms), OSPF vẫn giữ cost 10 và **không tự chuyển sang WAN2**, làm gián đoạn người dùng.
* **Cơ chế AIOps:** Prometheus & Bộ 3 Model AI phát hiện chất lượng WAN1 suy giảm và dự báo kéo dài $\rightarrow$ Auto-Responder tự động đổi OSPF cost WAN1 từ `10` lên `100` bằng lệnh `vtysh` $\rightarrow$ OSPF lập tức lái lưu lượng sang WAN2 (`cost 50`). Khi WAN1 ổn định, AIOps trả cost về `10`.

---

## 5. Cấu trúc thư mục dự án (5 Trụ cột Chuẩn hóa)

```text
AIOpsProject/
├── topo.yml                         # Topology Containerlab tổng thể
├── config/                          # [1. HẠ TẦNG MẠNG] Cấu hình FRR OSPF cho Core & Branch Router
│   ├── core-frr.conf
│   ├── branch-frr.conf
│   ├── daemons
│   └── vtysh.conf
├── app/                             # [2. SERVER DOANH NGHIỆP - LAN] Web Nginx + Flask API + PostgreSQL DB
│   ├── html/                        # Giao diện Corporate Web Portal
│   ├── api/                         # Flask API (:5000) & Prometheus /metrics
│   ├── db/                          # PostgreSQL init.sql (:5432)
│   └── nginx.conf                   # Nginx reverse proxy /api/ & /stub_status
├── monitoring/                      # [3. CỤM SERVER GIÁM SÁT & AIOPS - MẠNG OOB 172.20.20.0/24]
│   ├── prometheus/                  # Thu thập time-series metrics mỗi 5s (.51:9090)
│   ├── alertmanager/                # Quản lý cảnh báo & bắn webhook (.52:9093 -> .55:5000/alerts)
│   ├── grafana/                     # Dashboard trực quan hóa hạ tầng mạng (.53:3000)
│   └── aiops-dashboard/             # Web Trung tâm Chỉ huy AIOps & Tự động ứng phó (.55:5000)
│       ├── app.py                   # Backend điều phối 3 Model AI + Policy Engine + API + Webhook
│       ├── templates/index.html     # Giao diện Web AIOps Command Center
│       └── static/                  # style.css & dashboard.js
├── ai/                              # [4. KHỐI DỮ LIỆU & TRÍ TUỆ NHÂN TẠO (3 MODEL ĐỘC LẬP)]
│   ├── collector/                   # Pipeline thu thập Prometheus & hợp nhất Dataset
│   │   ├── generate_dataset.py      # Chạy kịch bản, query Prometheus API, xuất CSV gốc
│   │   └── prepare_two_stage_datasets.py # Hợp nhất 1 file Dataset duy nhất (`aiops_network_dataset.csv`)
│   ├── dataset/                     # Chứa bộ dataset chuẩn cho Local & Kaggle
│   │   ├── aiops_network_dataset.csv     # Dataset Hợp nhất duy nhất cho cả 3 Model (700 mẫu x 117 cột)
│   │   ├── raw_prometheus_timeseries.csv # Dữ liệu chuỗi thời gian Prometheus gốc backup (713 mẫu x 46 cột)
│   │   └── timeline.json                 # Mốc thời gian thực thi các kịch bản
│   ├── eif_model.py                 # Thuật toán Extended Isolation Forest (EIF)
│   ├── train.py                     # Huấn luyện 3 Model độc lập từ `ai/dataset/aiops_network_dataset.csv`
│   ├── evaluate.py                  # Kiểm thử Hệ thống điều phối 3 Model nối tiếp
│   ├── models/                      # Lưu model1_eif_detector.pkl, model2_xgboost_forecaster.pkl, model3_rca_classifier.pkl
│   └── reports/                     # Lưu báo cáo & 3 Kaggle Notebooks riêng biệt cho 3 Model
│       ├── Kaggle_Model1_EIF_Instant_Detector.ipynb   # Notebook 1: Extended Isolation Forest (14 features -> current_degraded)
│       ├── Kaggle_Model2_XGBoost_5m_Forecaster.ipynb  # Notebook 2: XGBoost 5m Forecaster (84 features -> degraded_forecast_5m)
│       ├── Kaggle_Model3_RCA_Classifier.ipynb         # Notebook 3: XGBoost Multi-class RCA (42 features -> label)
│       └── training_metrics.json                      # Báo cáo kết quả thực nghiệm JSON
├── scripts/                         # [5. VẬN HÀNH & KỊCH BẢN]
│   ├── deploy.sh                    # Khởi chạy lab & monitoring
│   ├── destroy.sh                   # Hủy lab & bridge
│   ├── verify.sh                    # Kiểm tra end-to-end (37/37 PASS)
│   ├── test-failover.sh             # Demo WAN failover OSPF
│   └── scenarios/                   # Bộ 7 script kịch bản sự cố & tấn công
└── docs/network-design.md           # Thiết kế mạng, sơ đồ Mermaid, bảng IP
```

---

## 6. KẾ HOẠCH DỰ KIẾN TIẾP THEO (HOÀN THIỆN GIAI ĐOẠN 5 & DEPLOY AZURE VM)

Mục tiêu cuối cùng là hoàn thiện hệ thống khép kín (**Closed-loop AIOps**) trên máy Ubuntu Local, sau đó `git push` lên GitHub để `git pull` chạy trực tiếp trên **máy ảo Ubuntu Azure** (public Web qua cổng `:5000` và `:3000`). Các hạng mục cụ thể cần làm tiếp:

### 🔜 Hạng mục 1: Xây dựng Tầng Cơ sở Dữ liệu Bền vững (`monitoring/aiops-dashboard/database.py`)
* **Thiết kế Hybrid Database (SQLite `aiops.db` Zero-Config + Hỗ trợ đồng bộ `MONGO_URI` MongoDB Atlas Cloud):**
  1. **Bảng / Collection `incidents`:** Lưu lịch sử từng lần suy luận của 3 Model (`timestamp`, `m1_eif_score`, `m1_degraded`, `m2_prob_5m`, `trigger_m3`, `m3_fault_class`, `m3_confidence`, snapshot thông số mạng).
  2. **Bảng / Collection `remediation_logs`:** Lưu bền vững nhật ký tự động ứng phó & khôi phục (`time`, `scenario`, `trigger_mode`, `target_node`, `command`, `status`).
  3. **Bảng / Collection `system_policy`:** Lưu cấu hình ngưỡng dự báo 5m (`forecast_threshold`), chế độ `auto_pilot`, đường truyền WAN hiện hành và danh sách IP đang bị chặn để không bị mất khi khởi động lại server.

### 🔜 Hạng mục 2: Kết nối Luồng Thời gian thực (Live Prometheus Scraping + Real OOB Remediation)
* Nâng cấp [`monitoring/aiops-dashboard/app.py`](monitoring/aiops-dashboard/app.py) và tách module thực thi `monitoring/aiops-dashboard/remediation.py` để hỗ trợ **2 chế độ hoạt động trên Web**:
  1. **🧪 Chế độ Mô phỏng (Simulation Replay Mode - Đã có):** Duyệt nhanh qua 8 kịch bản từ dataset để kiểm thử tức thì phản ứng của 3 Model.
  2. **🔴 Chế độ Thời gian thực (Live Production Mode - Cần bổ sung):**
     * Cứ mỗi `5s` truy vấn trực tiếp 42 chỉ số tức thời từ Prometheus API (`http://localhost:9090` / `http://172.20.20.51:9090`), duy trì bộ đệm cửa sổ trượt 5 mẫu để tính 70 đặc trưng động học (tổng 84 đặc trưng cho Model 2), và đưa qua 3 Model AI.
     * **Thực thi lệnh khắc phục thật qua OOB (`docker exec`):**
       * `WanCongestion` / `WanLossLatency`: Thực thi thật lệnh `vtysh` trên `clab-aiops-prod-core-router` & `clab-aiops-prod-branch-router` đổi `ip ospf cost 100` trên `eth2` (WAN1) để bẻ lái lưu lượng sang WAN2.
       * `SynFlood` / `SshBruteforce` / `HttpFlood`: Thực thi thật lệnh `iptables` trên `clab-aiops-prod-firewall` chặn IP `203.0.113.10` hoặc giới hạn tốc độ HTTP.
       * `PortScan`: Thực thi thật lệnh `iptables` trên `clab-aiops-prod-service-server` cô lập máy nội bộ `192.168.10.30`.
       * **Nút Rollback thật:** Gỡ bỏ các luật `iptables` đã thêm và trả `ip ospf cost 10` cho WAN1 trên Router.
     * **Bảng Nút Bấm Kích hoạt Kịch bản Thật (Live Scenario Launcher):** Cho phép bấm nút trên Web để chạy nền các script trong `scripts/scenarios/01..06_*.sh` phục vụ demo trực tiếp trước Hội đồng.

### 🔜 Hạng mục 3: Tích hợp vào `topo.yml` / `scripts/deploy.sh` & Đồng bộ lên Máy ảo Azure
* Tích hợp khởi chạy tự động `monitoring/aiops-dashboard/app.py` (hoặc container `aiops-server` tại `172.20.20.55:5000`) vào `scripts/deploy.sh` và thêm bài kiểm tra sức khỏe cổng `:5000` vào `scripts/verify.sh`.
* Kiểm tra `git status`, dọn dẹp các file rác/cache, commit và `git push` lên nhánh `main` để kéo về máy ảo Ubuntu trên Azure.
