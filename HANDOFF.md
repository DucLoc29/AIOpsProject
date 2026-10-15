# HANDOFF – Dự án AIOps (đồ án tốt nghiệp)

> File này để **một cửa sổ/agent mới đọc và làm tiếp** mà không cần hỏi lại.
> Đọc theo thứ tự: mục 1 → 2 → 3 → 6. Chi tiết IP: [`docs/network-design.md`](docs/network-design.md).
> Cập nhật lần cuối: 2026-10-15. Ngôn ngữ làm việc với người dùng: **tiếng Việt**.

---

## 1. Đề tài và yêu cầu

**Tên đề tài:** Nghiên cứu và xây dựng hệ thống giám sát, phát hiện bất thường và tự động ứng phó sự cố mạng dựa trên AIOps.

**Nội dung thực hành (5 yêu cầu của đề cương):**
1. Thiết kế, triển khai mô phỏng topo mạng doanh nghiệp trên môi trường Cloud.
2. Xây dựng pipeline thu thập số liệu thời gian thực bằng **Prometheus** và dashboard **Grafana**.
3. Xây dựng kịch bản mô phỏng các tình huống tấn công, sự cố để thu thập dữ liệu.
4. Huấn luyện và triển khai mô hình AI phân tích dữ liệu mạng để phát hiện sớm bất thường và phân loại sự cố.
5. Xây dựng module tự động phản ứng và xử lý sự cố.

**Nội dung lý thuyết liên quan:** mô hình AIOps và kiến trúc tự động hóa khép kín; giao thức thu thập dữ liệu và giám sát thời gian thực; ảo hóa hạ tầng bằng Container và Network as Code; ML/DL cho phát hiện bất thường chuỗi thời gian và lưu lượng mạng.

---

## 2. Môi trường

| Mục | Giá trị |
|---|---|
| Nền tảng | Azure VM, hostname `vm-aiops-lab`, user `azureuser` |
| Cấu hình | 2 vCPU, 7.7 GB RAM, ổ 29 GB (còn khoảng 20 GB) |
| Công cụ | Containerlab 0.79.0, Docker, quyền `sudo` |
| Thư mục dự án | `/home/azureuser/aiops-project` |
| Git | **Chưa có** (chưa là repo git) |

---

## 3. Trạng thái hiện tại

### Giai đoạn 1 – Topology: ✅ HOÀN THÀNH và đã kiểm chứng
Trước khi tắt máy: `./scripts/verify.sh` đạt **26/26 PASS**, `test-failover.sh` chạy đúng (cắt WAN1 thì chuyển WAN2 ngay; khôi phục WAN1 trong khoảng 1 giây).

### Giai đoạn 2 – Giám sát (Prometheus, Grafana): ⏳ CHƯA BẮT ĐẦU
Mới chỉ chốt thiết kế (mục 5). Chưa có file nào cho giai đoạn này.

### Giai đoạn 3, 4, 5: ⏳ CHƯA BẮT ĐẦU

### ⚠ Lab KHÔNG tự chạy sau khi VM khởi động lại
Containerlab không giữ trạng thái. Sau mỗi lần bật VM phải chạy lại:
```bash
cd ~/aiops-project && ./scripts/deploy.sh && sleep 10 && ./scripts/verify.sh
```
Tại thời điểm viết file này: lab `aiops-prod` **chưa chạy**, bridge `lan-switch` **chưa tồn tại**.

---

## 4. Cấu trúc thư mục

```
aiops-project/
├── topo.yml                  # Topology Containerlab (tên lab: aiops-prod)
├── config/
│   ├── core-frr.conf         # FRR cho core-router
│   ├── branch-frr.conf       # FRR cho branch-router
│   ├── daemons               # zebra, ospfd, staticd
│   └── vtysh.conf
├── scripts/
│   ├── deploy.sh             # Tạo bridge lan-switch rồi deploy (LUÔN dùng script này)
│   ├── destroy.sh            # Huỷ lab và xoá bridge
│   ├── verify.sh             # 26 bài kiểm tra end-to-end
│   └── test-failover.sh      # Demo đứt WAN1, chuyển WAN2, khôi phục
├── docs/network-design.md    # Sơ đồ Mermaid + bảng IP chi tiết từng thiết bị
└── HANDOFF.md                # File này
```
Thư mục `clab-aiops-prod/` do Containerlab tự sinh (có sẵn inventory cho Ansible/Nornir), không sửa tay.

---

## 5. Topology hiện tại (tóm tắt)

```
internet-attacker ── core-router ══(WAN1 cost10 / WAN2 cost50, OSPF)══ branch-router ── branch-client
                        │
                    firewall ── lan-switch ──┬── service-server (Nginx, mục tiêu)
                                             ├── client-pc
                                             └── aiops-server (hiện chỉ là Alpine trống)
```

| Node | Image | Data IP | Mgmt IP (eth0) | Vai trò |
|---|---|---|---|---|
| core-router | frrouting/frr | 10.0.1.1/30, 10.0.12.2/30, 10.0.13.2/30, 203.0.113.1/24 | 172.20.20.11 | OSPF RID 2.2.2.2 |
| branch-router | frrouting/frr | 172.16.1.1/24, 10.0.12.1/30, 10.0.13.1/30 | 172.20.20.12 | OSPF RID 1.1.1.1 |
| firewall | alpine + iptables | 10.0.1.2/30 (outside), 192.168.10.1/24 (inside) | 172.20.20.13 | Gateway LAN |
| service-server | nginx:alpine | 192.168.10.10/24 | 172.20.20.21 | Mục tiêu tấn công |
| aiops-server | alpine | 192.168.10.20/24 | 172.20.20.22 | Sẽ thay bằng cụm AIOps |
| client-pc | alpine | 192.168.10.30/24 | 172.20.20.23 | Người dùng LAN, **attacker nội bộ** |
| branch-client | alpine | 172.16.1.10/24 | 172.20.20.31 | Sinh tải, **attacker chi nhánh** |
| internet-attacker | alpine | 203.0.113.10/24 | 172.20.20.41 | **Attacker từ Internet** |

- Tên container: `clab-aiops-prod-<tên node>` (ví dụ `clab-aiops-prod-core-router`).
- Mạng quản lý: Docker network **`clab`**, dải `172.20.20.0/24`, gateway `.1`. (Dải này đã bị mạng `clab` mặc định chiếm nên `topo.yml` dùng lại nó, đừng tạo network tên khác.)
- 3 node attacker có sẵn `hping3`, `nmap`, `iperf3`, `curl`.

### Quyết định thiết kế đã chốt (kèm lý do, để trả lời khi bảo vệ)
1. **OOB management:** Prometheus thu metric qua `eth0` (172.20.20.0/24), tách khỏi data plane, để khi bị tấn công hay đứt link hệ thống giám sát vẫn sống.
2. **3 hướng tấn công:** Internet (`internet-attacker`), chi nhánh (`branch-client`), nội bộ (`client-pc`). Hai hướng đầu đi qua firewall, nên chặn tại firewall. Hướng nội bộ **không qua firewall**, nên phải chặn ngay trên `service-server`. Module phản ứng cần chọn điểm chặn theo nguồn.
3. **Không tách vùng DMZ/LAN/MGMT:** ngoài phạm vi đề tài, ghi vào "Hướng phát triển" của báo cáo.
4. **Node "Net"** trên sơ đồ gốc (draw) được hiện thực bằng `internet-attacker`.
5. **OSPF point-to-point trên 2 link WAN** (không bầu DR/BDR) giúp hội tụ khoảng 1s thay vì khoảng 40s.
6. Cổng nối host/firewall/Internet đặt OSPF **passive**.
7. Host giữ default route trên `eth0` (để `apk add` ra Internet thật), các dải trong lab dùng route cụ thể qua data plane.

---

## 6. VIỆC CẦN LÀM TIẾP: Giai đoạn 2

### 6.1 Kiến trúc đề xuất (người dùng đã đồng ý hướng tách node riêng)
Thay `aiops-server` bằng cụm container riêng, **chỉ cắm mạng quản lý `eth0`**, không cắm vào `lan-switch`:

| Node | Image | Tầng | Mgmt IP | Cổng |
|---|---|---|---|---|
| prometheus | prom/prometheus | Thu thập + lưu trữ | 172.20.20.51 | 9090 |
| alertmanager | prom/alertmanager | Cảnh báo theo luật, gửi webhook | 172.20.20.52 | 9093 |
| grafana | grafana/grafana | Web giám sát | 172.20.20.53 | 3000 |
| ai-engine | python (tự build) | Phát hiện và phân loại (GĐ4) | 172.20.20.54 | 8000 |
| auto-responder | python (tự build) | Phản ứng (GĐ5) | 172.20.20.55 | 5000 |

Exporter đề xuất: `node_exporter` (các host/router), `frr_exporter` hoặc SNMP (OSPF neighbor, traffic interface), `nginx-prometheus-exporter` (web server).

```
Router/Firewall/Server + exporter ──scrape──> Prometheus ──> Grafana
                                                   ├──> Alertmanager ──> Auto-Responder
                                                   └──> AI Engine ─────> Auto-Responder
Auto-Responder ──(iptables / vtysh / restart)──> thiết bị mạng
```

### 6.2 Các bước
1. **Dọn lab trước:** `./scripts/deploy.sh` rồi `./scripts/verify.sh` để chắc nền ổn.
2. Quyết định còn mở (hỏi người dùng nếu chưa rõ): (a) gộp hay tách `ai-engine` và `auto-responder` (đề xuất: tách); (b) bỏ node `aiops-server` cũ (đề xuất: bỏ).
3. Thêm 3 node `prometheus`, `alertmanager`, `grafana` vào `topo.yml` (mgmt IP cố định như bảng trên, bind-mount cấu hình từ thư mục mới `monitoring/`).
4. Cài exporter lên các node, mở cổng metric (9100 cho node_exporter, v.v.).
5. Viết `monitoring/prometheus/prometheus.yml` (scrape các mgmt IP `172.20.20.x`), rule cảnh báo, và dashboard Grafana (provisioning bằng file, để dashboard không mất khi deploy lại).
6. Truy cập Grafana từ ngoài: mở cổng 3000 trên Azure NSG, hoặc dùng SSH tunnel `ssh -L 3000:172.20.20.53:3000 azureuser@<IP-public>` (an toàn hơn).
7. Thêm bước kiểm tra Prometheus (`/api/v1/targets` đều `up`) vào `scripts/verify.sh`.
8. Cập nhật `docs/network-design.md` (sơ đồ và bảng IP) và file này.

### 6.3 Các giai đoạn sau (tóm tắt)
- **GĐ3 – Kịch bản sự cố** (script tự ghi mốc thời gian để **gán nhãn dữ liệu**): đứt WAN1, SYN flood bằng `hping3`, quét cổng `nmap`, nghẽn băng thông `iperf3`, độ trễ/mất gói `tc netem`, server quá tải. Phải chạy tải bình thường xen kẽ để có dữ liệu "lành".
- **GĐ4 – AI:** phát hiện bất thường (Isolation Forest, LSTM-Autoencoder); phân loại sự cố (Random Forest, XGBoost) trên dữ liệu đã gán nhãn. Cân nhắc huấn luyện offline vì VM chỉ 2 vCPU.
- **GĐ5 – Tự động phản ứng:** Alertmanager hoặc AI gửi webhook, `auto-responder` dùng Nornir/Ansible (inventory sẵn trong `clab-aiops-prod/`) để chặn IP bằng iptables, đổi OSPF cost, restart dịch vụ.

---

## 7. Lưu ý và bẫy đã gặp (đọc kỹ để tránh lặp lại)

1. **Luôn dùng `./scripts/deploy.sh`**, không gọi thẳng `containerlab deploy`: bridge `lan-switch` phải tồn tại trước và sẽ mất khi VM reboot.
2. **Quyền file `config/*`:** FRR đổi owner file cấu hình khi container chạy, khiến `azureuser` không đọc được. `deploy.sh` đã tự `chmod 644 config/*`. Nếu vẫn lỗi: `sudo chown azureuser:azureuser config/* && sudo chmod 644 config/*`.
3. **Từng có lab cũ `aiops-net`** (container `r1`, `r2`, `r3`, `server`, `client`, đặt `restart=always`) làm các thư mục rỗng `r1/ r2/ r3/` tự sinh lại mỗi lần boot. Đã được dọn; nếu thư mục này xuất hiện lại thì kiểm tra `sudo docker ps -a`.
4. **Subnet 172.20.20.0/24 đã thuộc network `clab`:** không đặt `mgmt.network` tên khác trong `topo.yml`.
5. **Alpine:** không có `iptables`/`hping3` sẵn; `hping3` phải lấy từ repo `edge/testing` (đã có trong `exec` của `topo.yml`). Các `exec` cần Internet qua `eth0` nên deploy lần đầu hơi lâu (khoảng 1,5 phút).
6. **Lệnh `ping_test` trong `verify.sh` là hàm nội bộ của script**, không gõ được ngoài terminal. Ping thủ công: `sudo docker exec clab-aiops-prod-client-pc ping -c4 192.168.10.10`.
7. **Lệnh nhiều dòng dài dán vào terminal có thể bị cắt/lỗi**: đặt vào file script thay vì chạy trực tiếp.
8. FRR trong container: dùng `sudo docker exec -it clab-aiops-prod-core-router vtysh` (lệnh `show ip ospf neighbor`, `show ip route`).
9. Server từng bị restart giữa phiên làm việc: mọi tiến trình nền bị dừng. Hãy lưu mọi thứ vào file trong dự án, đừng chỉ để trong terminal.

---

## 8. Chưa làm / khuyến nghị

- [ ] **Khởi tạo Git** (`git init`, commit, push GitHub/GitLab) để không mất công sức. Hiện dự án chưa có version control.
- [ ] Vẽ lại sơ đồ chính thức (EVE-NG style ban đầu có `e0/x`) theo cổng `ethX` thực tế, thêm mạng OOB bằng nét đứt, ghi chú IP đầy đủ. Có thể xuất PNG/SVG từ Mermaid trong `docs/network-design.md`.
- [ ] Đề xuất nâng VM lên 4 vCPU nếu huấn luyện LSTM trực tiếp trên VM.
- [ ] Làm mục "Hướng phát triển" cho báo cáo: tách vùng DMZ/LAN/MGMT, thay switch câm bằng OVS, thu thập SNMP/NetFlow.
