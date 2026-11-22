# HANDOFF – Dự án AIOps (Đồ án tốt nghiệp)

> File này để **teammate hoặc một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 4 (đặc biệt đọc kỹ **mục 4** trước khi sửa code). Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-11-21. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

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

---

## 3. Nhiệm vụ Bàn giao cho Teammate: Đăng nhập Phân quyền (RBAC) + ChatOps Telegram & Cảnh báo Leo thang

> **Mục tiêu:** Bổ sung tầng bảo mật vận hành (Authentication + RBAC) và kênh thông báo tương tác **Telegram ChatOps** theo mô hình **Cảnh báo Leo thang Đa tầng (Multi-Tier Alert Escalation Policy)** mà **không làm ảnh hưởng** tới lõi thu thập thật, 2 model AI và cơ chế tự chặn `iptables`.

### 3.1. Kiến trúc Module Đề xuất (Tách độc lập, chỉ hook nhẹ vào `app.py`)
Tạo mới đúng **2 module độc lập** trong `monitoring/aiops-dashboard/backend/`:
1. **`auth.py`** — Quản lý tài khoản, xác thực phiên & phân quyền RBAC.
2. **`notifier.py`** — Telegram Bot API (Long Polling) + Inline Buttons phê duyệt trực tiếp + Bộ máy Leo thang `L1 → L2 → L3` + Thông báo `Auto-Resolved`.

Các biến môi trường mới cần khai báo mẫu trong [`.env.example`](.env.example) (và điền giá trị thật ở `.env` cục bộ, **KHÔNG commit `.env`**):
```env
AUTH_SECRET=thay_bang_chuoi_bi_mat_dai_ngau_nhien
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_L2_CHAT_ID=
TELEGRAM_ADMIN_IDS=
ESCALATION_L2_SECONDS=30
ESCALATION_L3_SECONDS=60
```
- **Yêu cầu Zero-Crash:** Nếu `.env` chưa điền `TELEGRAM_BOT_TOKEN` hoặc `TELEGRAM_CHAT_ID`, backend phải tự động bỏ qua kênh Telegram (chỉ in 1 dòng thông báo khi khởi động), tuyệt đối không làm sập server.

### 3.2. Hướng dẫn triển khai Đăng nhập & Phân quyền (`auth.py` + Frontend)
- **2 vai trò chuẩn NOC/SOC:**
  - **`ADMIN`:** Toàn quyền xem và thao tác (`POST /api/policy`, `POST /api/approve`, `POST /api/rollback`, CRUD `POST/PUT/DELETE /api/services`).
  - **`VIEWER`:** Chỉ đọc (`GET /api/state`, `GET /api/database`, `GET /api/services`, `GET /api/auth/me`). Mọi request thay đổi trạng thái từ `VIEWER` phải bị Backend từ chối với **`403 Forbidden`** (không chỉ ẩn nút ở Frontend).
- **Lưu trữ & Bảo mật (không cần cài thêm thư viện ngoài):**
  - Thêm collection `Users` vào [`monitoring/aiops-dashboard/backend/database.py`](monitoring/aiops-dashboard/backend/database.py) (seed sẵn 2 tài khoản mặc định khi collection rỗng: `admin` và `viewer`).
  - Băm mật khẩu bằng `hashlib.pbkdf2_hmac("sha256", ...)` + salt ngẫu nhiên (`secrets.token_hex(16)`).
  - Cấp phiên đăng nhập bằng **Signed Token (`HMAC-SHA256` ký bằng `AUTH_SECRET`, có thời hạn `exp`)** gửi qua header `Authorization: Bearer <token>` (hoặc Cookie `HttpOnly`).
  - Ghi rõ trường **`actor`** (vd: `admin (Web)`, `Telegram (@username)`, `Escalation-L3`) vào `audit_logs` và `RemediationActions` để có Audit Trail đầy đủ.
- **API xác thực thêm vào `app.py`:**
  - `POST /api/auth/login` → xác thực tài khoản, trả `{token, user: {username, role, full_name}}`.
  - `POST /api/auth/logout` → đăng xuất phiên.
  - `GET /api/auth/me` → trả thông tin user hiện tại (hoặc `401` nếu chưa đăng nhập).
- **Lưu ý quan trọng để không làm hỏng `./scripts/verify.sh`:**
  - [`scripts/verify.sh`](scripts/verify.sh) dòng 99 gọi `curl -sf -m3 http://127.0.0.1:5000/api/state | grep -q '"model1"'` không kèm token.
  - Do đó nên giữ `GET /api/state` đọc được từ localhost (hoặc thêm `/api/health` công khai và cập nhật tương ứng trong `scripts/verify.sh`), còn toàn bộ API ghi (`POST/PUT/DELETE`) và thao tác quản trị bắt buộc kiểm tra token + role `ADMIN`.
- **Frontend (`monitoring/aiops-dashboard/frontend/src/`):**
  - Thêm trang `LoginPage.jsx` theo cùng phong cách tối giản Apple (`styles.css`); gắn `Authorization: Bearer <token>` trong `api.js` (nếu nhận `401` thì tự chuyển về trang đăng nhập).
  - Trên `Sidebar.jsx`: hiển thị tên tài khoản + badge vai trò (`ADMIN` / `VIEWER`) + nút **Đăng xuất**.
  - Với `VIEWER`: ẩn hoặc khóa nút `Phê duyệt`, `Gỡ chặn`, chuyển `Auto-Pilot / Human-in-the-Loop`, và các nút `+ Thêm / Sửa / Xóa dịch vụ`.

### 3.3. Hướng dẫn triển khai Telegram ChatOps & Cảnh báo Leo thang (`notifier.py`)
- **Bắt buộc dùng Long Polling (`getUpdates`), KHÔNG dùng Webhook:**
  - Chạy vòng lặp `getUpdates?timeout=25` ở **1 background daemon thread riêng** bằng `urllib.request` (không cần mở port inbound, không cần HTTPS domain/SSL, chạy được ngay trên cả máy Local lẫn Azure VM).
- **Cơ chế nút bấm Inline (`InlineKeyboardMarkup`) & Chống bấm đúp:**
  - Khi gửi cảnh báo ở chế độ `Human-in-the-Loop`, đính kèm 2 nút:
    - `[✅ Phê duyệt & Chặn ngay]` (`callback_data = "approve:<service_id>"`)
    - `[❌ Bỏ qua]` (`callback_data = "dismiss:<service_id>"`)
  - Khi đã chặn (Auto-Pilot hoặc sau khi duyệt), đính kèm nút `[↩️ Gỡ chặn (Rollback)]` (`callback_data = "rollback:<service_id>"`).
  - **Bảo mật ChatOps:** Chỉ xử lý `callback_query` khi `from.id` hoặc `chat.id` nằm trong `TELEGRAM_ADMIN_IDS` / `TELEGRAM_CHAT_ID` / `TELEGRAM_L2_CHAT_ID`.
  - Ngay khi nhận bấm nút: gọi `answerCallbackQuery`, thực thi `ENGINE.approve_pending(sid)` hoặc `ENGINE.rollback_service_or_all(sid)`, rồi gọi **`editMessageText`** để gỡ bàn phím inline và cập nhật nội dung gốc (vd: *"✅ Đã phê duyệt bởi @username lúc HH:MM:SS"*) nhằm chống bấm đúp. Nếu quản trị viên đã duyệt trên Web trước đó, cập nhật luôn tin nhắn Telegram tương ứng.
- **Chính sách Cảnh báo Leo thang 4 tầng + Auto-Resolved:**
  1. **Tầng 0 — Lọc nhiễu (`SUPPRESSED`):** `TransientSpike` (`MEDIUM` + dự báo 5m `NORMAL`) → **TUYỆT ĐỐI KHÔNG gửi Telegram** (chống Alert Fatigue).
  2. **Tầng 1 (L1 Alert — `t = 0s`):** Khi Model 2 chẩn đoán sự cố (`HIGH`/`CRITICAL`):
     - `Human-in-the-Loop`: gửi cảnh báo đầy đủ *(Dịch vụ, Mức độ, Nguyên nhân + độ tin cậy, IP nguồn truy vết động, Điểm chặn Firewall/Host, Lệnh `iptables` dự kiến)* kèm nút Phê duyệt / Bỏ qua tới `TELEGRAM_CHAT_ID`.
     - `Auto-Pilot`: tự chặn ngay và gửi thông báo báo cáo kèm nút `[↩️ Gỡ chặn]`.
  3. **Tầng 2 (L2 Escalation — sau `ESCALATION_L2_SECONDS`, mặc định demo `30s`):** Nếu sự cố vẫn ở `pending_approvals` (chưa ai duyệt trên Web hay Telegram) và dịch vụ vẫn đang suy giảm → gửi **⚠️ [LEO THANG TẦNG 2 — CHƯA XỬ LÝ]** tới `TELEGRAM_L2_CHAT_ID` (nếu để trống thì gửi vào `TELEGRAM_CHAT_ID` để dễ demo 1 điện thoại).
  4. **Tầng 3 (L3 Fail-Safe Auto-Mitigation — sau `ESCALATION_L3_SECONDS`, mặc định demo `60s`):**
     - Nếu quá hạn vẫn chưa có phản hồi, dịch vụ vẫn `HIGH`/`CRITICAL`, `fault_class != "Unknown_Anomaly"` và độ tin cậy `>= 80%` → tự động gọi `ENGINE.approve_pending(sid)` để chặn khẩn cấp cứu dịch vụ, đồng thời gửi **🚨 [LEO THANG TẦNG 3 — ĐÃ TỰ ĐỘNG BẢO VỆ]** kèm nút `[↩️ Gỡ chặn]`.
     - **Quy tắc an toàn bắt buộc:** Riêng **`Unknown_Anomaly`** (độ tin cậy `< 70%` hoặc dịch vụ sập không có lưu lượng tấn công) thì **KHÔNG BAO GIỜ tự chặn ở Tầng 3**, chỉ gửi cảnh báo leo thang yêu cầu quản trị viên kiểm tra thủ công.
  5. **Thông báo Hồi phục (`Auto-Resolved`):** Khi dịch vụ từng cảnh báo quay về `NORMAL` ổn định (≥ 2 chu kỳ liên tiếp), gửi 1 tin nhắn gọn **🟢 [ĐÃ HỒI PHỤC]** để đóng vòng đời sự cố.
  - Có thể hiển thị thêm nhãn tầng leo thang (`L1` / `L2` / `L3`) ngay trên thẻ chờ phê duyệt ở trang [`ResponsePage.jsx`](monitoring/aiops-dashboard/frontend/src/pages/ResponsePage.jsx).

### 3.4. Nối thông Alertmanager Webhook (Tuyến cảnh báo hạ tầng cứng → Web & Telegram)
- **Vai trò:** Prometheus (`172.20.20.51:9090`) đánh giá 5 luật hạ tầng cứng trong [`monitoring/prometheus/rules/network_alerts.yml`](monitoring/prometheus/rules/network_alerts.yml) (`HostDown`, `WANLinkDown`, `HighPacketRate`, `HighPacketDropRate`, `HighCpuUsage`) và gửi sang Alertmanager (`172.20.20.52:9093`) để gom nhóm, chống lặp và bắn Webhook về Backend `POST /alerts` — bổ trợ cho tuyến AI ở tầng dịch vụ.
- **2 điểm cần sửa để nối thông thật:**
  1. Trong [`monitoring/alertmanager/alertmanager.yml`](monitoring/alertmanager/alertmanager.yml) dòng 25, đổi `url: 'http://172.20.20.55:5000/alerts'` thành **`url: 'http://172.20.20.1:5000/alerts'`** (IP gateway mạng OOB `clab` trỏ về máy chủ host đang chạy `app.py`; không còn container `.55`). Lưu ý giữ endpoint `POST /alerts` nhận được từ `172.20.20.52` (không bị chặn bởi auth token của người dùng Web).
  2. Trong `POST /alerts` của [`monitoring/aiops-dashboard/backend/app.py`](monitoring/aiops-dashboard/backend/app.py), thay vì chỉ lưu tạm vào `ENGINE.alertmanager_webhooks`, hãy parse danh sách `body.get("alerts", [])`, ghi cảnh báo hạ tầng (`firing` / `resolved`) vào `audit_logs` + `Alerts` (MongoDB Atlas) và đẩy tiếp sang `notifier.py` để gửi thông báo hạ tầng (`HostDown`, `WANLinkDown`, `HighCpuUsage`...) ra cùng kênh Telegram (chỉ thông báo, **không** tự chạy `iptables` từ webhook hạ tầng tĩnh).

---

## 4. Quy tắc Bắt buộc & Vùng Cấm Động (Tránh Làm Hỏng Hệ thống Hiện tại)

### 🚫 4.1. Danh sách file / module KHÔNG ĐƯỢC CHỈNH SỬA
1. **Toàn bộ thư mục `ai/`** (`eif_model.py`, `train.py`, `evaluate.py`, `kaggle_train_*.py`, `collector/prepare_two_stage_datasets.py`, `dataset/*.csv`, `models/*.pkl`, `reports/training_metrics.json`):
   - Dataset 100% đo thật và 2 model AI đã huấn luyện, hiệu chuẩn 4 ngưỡng và nghiệm thu xong — **không sửa đặc trưng, không đổi ngưỡng, không train đè**.
2. **[`monitoring/aiops-dashboard/backend/telemetry.py`](monitoring/aiops-dashboard/backend/telemetry.py):**
   - **Không sửa** công thức 6 SLA, chuỗi đếm kernel `AIOPS_ACCT_IN` / `AIOPS_ACCT_OUT`, bộ đệm trượt `6s`, hay thuật toán truy vết IP nguồn `detect_attacker_ips()`.
   - **Tuyệt đối không** đưa lại `rng.normal` hay bất kỳ nhánh `if/elif` giả lập số liệu nào.
3. **[`monitoring/aiops-dashboard/backend/remediation.py`](monitoring/aiops-dashboard/backend/remediation.py):**
   - **Không gắn cứng lại IP attacker** theo tên kịch bản.
   - **Không sửa** logic phân hướng East–West LAN (`INPUT 1` trên server đích) vs North–South (`FORWARD 1` trên `firewall`), **không sửa `CORE_WHITELIST_IPS`** (`192.168.10.1/10/20/21`, `172.20.20.0/24`), và **không xóa mất chuỗi đếm `AIOPS_ACCT_IN`** khi rollback.
4. **Hạ tầng & kịch bản:** [`topo.yml`](topo.yml), `config/`, `app/`, `monitoring/prometheus/`, `monitoring/grafana/`, `scripts/scenarios/00..05`:
   - Không đổi IP/port, không đổi `scrape_interval: 2s`, không xóa `PerSourcePenaltyExemptList 192.168.10.1` trong `topo.yml`, không sửa cơ chế `trap` dừng sạch tiến trình trong các script `01..05`.

### ⚠ 4.2. Các nguyên tắc kỹ thuật bắt buộc khi viết code mới
- **KHÔNG chặn `ENGINE._lock` bằng gọi mạng ngoài:** Mọi lời gọi tới **Telegram API** hoặc **MongoDB Atlas** phải chạy trong **background thread riêng** (có `timeout=4` và `try/except`), tuyệt đối không gọi đồng bộ bên trong `with self._lock:` hay luồng polling 2s — giữ độ trễ `GET /api/state` và `GET /api/database` dưới `10ms` để Web không bị lag.
- **KHÔNG thêm lại nút kích hoạt kịch bản hay dropdown live/giả lập** trên giao diện Web hay API `/api/simulate` (đã chủ đích loại bỏ để hệ thống thuần giám sát thời gian thực).
- **KHÔNG commit `.env`** lên Git (chỉ cập nhật key mẫu rỗng ở `.env.example`).
- **Luôn build lại React (`frontend/dist/`) và commit cả thư mục `dist/`** sau mỗi lần sửa Frontend (vì máy Azure VM chạy trực tiếp bản build sẵn mà không cần cài Node.js).
- **Ưu tiên thư viện chuẩn Python** (`urllib.request`, `hashlib`, `hmac`, `secrets`, `threading`); nếu bắt buộc dùng thư viện ngoài thì phải thêm vào [`requirements.txt`](requirements.txt).

### ✅ 4.3. Checklist Nghiệm thu bắt buộc trước khi merge / push
1. `./scripts/verify.sh` đạt **40 PASS / 0 FAIL**.
2. `python3 ai/evaluate.py` đạt **6/6 kịch bản PASS**.
3. Chạy thử khi **chưa cấu hình `TELEGRAM_BOT_TOKEN`**: hệ thống vẫn chạy bình thường, không lỗi.
4. Kiểm thử **RBAC**: tài khoản `VIEWER` gọi `POST /api/approve`, `/api/rollback`, `/api/policy`, `/api/services` bị trả `403`; tài khoản `ADMIN` thao tác bình thường; `GET /api/state` phản hồi `< 10ms`.
5. Kiểm thử **Telegram ChatOps + Leo thang thật** bằng chạy `bash scripts/scenarios/01_internet_syn_flood.sh 70` (ở chế độ `Human-in-the-Loop`):
   - Bấm `[✅ Phê duyệt]` trên Telegram → Web chuyển `PROTECTED`, `iptables` chặn đúng IP/điểm chặn, dịch vụ tự về `NORMAL`, tin nhắn Telegram xóa nút cũ và gửi thêm `🟢 ĐÃ HỒI PHỤC`.
   - Không bấm nút → sau `30s` nhận cảnh báo **L2**, sau `60s` tự kích hoạt **L3 Auto-Mitigation** và tự hồi phục về `NORMAL`.
   - Chạy `bash scripts/scenarios/05_transient_spike.sh` → **0 tin nhắn Telegram** (bị lọc ở Tầng 0).
6. Kiểm tra `git status` chắc chắn **không lọt `.env`** và không có chỉnh sửa ngoài phạm vi (`auth.py`, `notifier.py`, `app.py`, `database.py`, `monitoring/alertmanager/alertmanager.yml`, `.env.example`, `frontend/src/`, `frontend/dist/`).
