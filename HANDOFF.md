# HANDOFF – Dự án AIOps (Đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-11-20. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Kiến trúc Hoàn chỉnh (100% Dữ liệu thật Từ Đầu Đến Cuối — Không Giả Lập)

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

### ✅ 1.1. Dataset Thực nghiệm 100% Thật & Hai Mô hình AI (`ai/models/`)
- **Dataset gốc 100% đo thật từ Containerlab (3 chu kỳ độc lập, KHÔNG `rng.normal`):**
  - Chuỗi thô: [`ai/dataset/raw_service_6sla_timeseries.csv`](ai/dataset/raw_service_6sla_timeseries.csv) (**2.656 mẫu thật**).
  - Dataset cửa sổ trượt 5 bước hợp nhất: [`ai/dataset/aiops_network_dataset.csv`](ai/dataset/aiops_network_dataset.csv) (**2.188 mẫu × 43 cột**: `Normal: 1348`, `PortScan: 288`, `HttpFlood: 252`, `SynFlood: 150`, `SshBruteforce: 150`, bao gồm `12` mẫu `TransientSpike` thật từ `05_transient_spike.sh`).
- **Model 1 — [`ai/models/model1_eif_detector.pkl`](ai/models/model1_eif_detector.pkl) (Extended Isolation Forest · 18 đặc trưng trượt = 6 SLA × 3 đại lượng):**
  - Huấn luyện theo đúng kiến trúc [`ai/kaggle_train_model1_eif.py`](ai/kaggle_train_model1_eif.py) / [`ai/train.py`](ai/train.py) (chia 60/20/20, fit `RobustScaler` chỉ trên Normal, xuất `eif_state` thuần).
  - Hiệu năng tập Test thật: **`Accuracy = 98.40%`**, **`Precision = 96.53%`**, **`Recall = 99.40%`**, **`F1 = 0.9795`**, **`ROC-AUC = 0.9987`**, **`PR-AUC = 0.9979`** (vượt trội Standard IF: `Acc = 95.21%`, `F1 = 0.9369`).
  - Ngưỡng 4 mức hiệu chuẩn: `NORMAL < 0.5451`, `MEDIUM [0.5451, 0.6061)`, `HIGH [0.6061, 0.6709)`, `CRITICAL >= 0.6709`.
- **Công thức Dự báo Xu hướng 5 phút (`compute_5m_trend_forecast`):**
  - $\hat{S}_{t+5m} = \text{EMA}(S_t) + \alpha \cdot \text{Slope}(S_{t-W..t}) \cdot R_{\text{sustained}}$ hiệu chuẩn trực tiếp theo 4 ngưỡng của Model 1 để tách sự cố kéo dài khỏi `TransientSpike`.
- **Model 2 — [`ai/models/model2_rca_classifier.pkl`](ai/models/model2_rca_classifier.pkl) (XGBoost Multi-class RCA · 36 đặc trưng trượt = 6 SLA × 6 đại lượng):**
  - Huấn luyện theo đúng kiến trúc [`ai/kaggle_train_model2_rca.py`](ai/kaggle_train_model2_rca.py) / [`ai/train.py`](ai/train.py) cho 5 lớp (`Normal`, `SynFlood`, `HttpFlood`, `SshBruteforce`, `PortScan`).
  - Hiệu năng tập Test thật: **`Accuracy = 100.00%`**, **`Macro-F1 = 1.0000`**, **`5-Fold CV F1 = 0.9941`**.
  - Áp dụng ngưỡng tự tin `confidence_threshold = 0.70` (dưới 70% hoặc dịch vụ sập không có lưu lượng tấn công → `Unknown_Anomaly`, chỉ cảnh báo quản trị viên và không tự chặn nhầm).

### ✅ 1.2. Pipeline Thu thập Vật lý Thật 100% (`telemetry.py` — Single Source of Truth)
- [`monitoring/aiops-dashboard/backend/telemetry.py`](monitoring/aiops-dashboard/backend/telemetry.py) dùng chung duy nhất cho cả [`ai/collector/prepare_two_stage_datasets.py`](ai/collector/prepare_two_stage_datasets.py) lẫn [`monitoring/aiops-dashboard/backend/app.py`](monitoring/aiops-dashboard/backend/app.py), **tuyệt đối không có `if/elif` phân loại kịch bản trước AI**:
  1. **Data-Plane Active Probe Burst** chạy trực tiếp từ `firewall` (`192.168.10.1` → `192.168.10.x:port`) qua đúng LAN Switch & `eth1` (hỗ trợ mọi dịch vụ CRUD mới và không bị ảnh hưởng bởi luật chặn IP attacker).
  2. **Kernel Per-Port & Per-Source Accounting (`iptables` chains `AIOPS_ACCT_IN` / `AIOPS_ACCT_OUT`)** trên `service-server`, `app-server`, `db-server` với bộ đệm tốc độ trượt `6s` (loại trừ IP probe `192.168.10.1`, tách sạch theo từng cổng `:80`, `:22`, `:5000`, `:5432`, gói SYN/RST cổng đóng và bộ đếm theo từng IP nguồn từ cả 3 hướng Internet/Branch/LAN).
  3. **Prometheus (`:9090`, `scrape_interval: 2s`)** thu thập `api_request_duration_seconds`, `nginx_http_requests_total` và thông số hạ tầng.
- **Truy vết IP nguồn ĐỘNG & Tự động chọn điểm chặn theo hướng tấn công (`detect_attacker_ips` + `build_service_remediation_plan`):**
  - Không gắn cứng IP theo kịch bản; tự truy vết IP thật (kể cả tấn công đa nguồn DDoS và lưu lượng qua Nginx reverse proxy) và tự chọn điểm đặt luật tối ưu:
    - **North–South (Internet `203.0.113.x` / Branch `172.16.1.x`):** chặn tại biên `clab-aiops-prod-firewall` (`FORWARD 1`).
    - **East–West (Insider LAN `192.168.10.x`):** chặn trực tiếp tại host đích (`INPUT 1`) vì lưu lượng không đi qua Firewall.
- **6 kịch bản chuẩn hóa (`scripts/scenarios/00..05`) & Vòng lặp khép kín đã kiểm chứng 100% thực tế từ Terminal ngoài (`40 PASS / 0 FAIL`):**
  - `00_normal_traffic.sh`, `01_internet_syn_flood.sh`, `02_insider_port_scan.sh`, `03_ssh_bruteforce.sh`, `04_server_http_flood.sh`, `05_transient_spike.sh` (đã loại bỏ `01_wan_congestion.sh` & `02_wan_loss_latency.sh` dư thừa; xử lý sạch `Ctrl+C` không sót tiến trình).
  - `TransientSpike`: bị chặn báo giả (0 luật `iptables`).
  - `SynFlood`, `SshBruteforce`, `PortScan`, `HttpFlood` (từ bất kỳ hướng nào: Internet, Branch, Insider LAN, hay đa nguồn đồng thời): tự phát hiện sau **2–12s**, tự thực thi `iptables` thật đúng IP:Port / đúng điểm chặn (Firewall hoặc Host), và **tự hồi phục về `Bình thường (NORMAL)` sau 10–18s ngay khi kịch bản tấn công vẫn đang chạy**.

---

## 2. Môi trường & Quy trình Khởi động (Local VM & Azure VM)

| Mục | Giá trị |
|---|---|
| Thư mục dự án | `/home/dash/Desktop/AIOpsProject` (Local) / `~/aiops-project` (Azure VM) |
| Git Repo | `https://github.com/DucLoc29/AIOpsProject.git` (nhánh `main`) |
| Cloud Database | **MongoDB Atlas** (`AIOpsProject`: `Services`, `AlertRules`, `AIResults`, `Alerts`, `RemediationActions`) |
| Giao diện | **AIOps Command Center**: `http://localhost:5000` · **Grafana**: `http://localhost:3000` · **Prometheus**: `http://localhost:9090` |

### ⚠ Quy trình khởi động sau khi reboot hoặc trên Azure VM
```bash
sudo ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```

Thu thập lại Dataset thật / Huấn luyện lại local / Kiểm thử pipeline:
```bash
python3 ai/collector/prepare_two_stage_datasets.py --cycles 3 --interval 2.0
python3 ai/train.py
python3 ai/evaluate.py
```

Build lại Frontend React (nếu có sửa `monitoring/aiops-dashboard/frontend/src/`):
```bash
/home/dash/.local/node/bin/node /home/dash/Desktop/AIOpsProject/monitoring/aiops-dashboard/frontend/node_modules/vite/bin/vite.js build /home/dash/Desktop/AIOpsProject/monitoring/aiops-dashboard/frontend
```
