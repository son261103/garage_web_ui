# Garage Web UI — Giao diện quản trị Garage (tiếng Việt)

Giao diện web tự viết để quản trị Garage trên server `roser`, thay cho việc
gõ lệnh CLI hay dùng garage-webui của bên thứ ba. Toàn bộ giao diện bằng
tiếng Việt, chỉ 1 màu chủ đạo hồng đặc sáng (`#ec4899`, KHÔNG gradient),
có chế độ sáng / tối (chọn ở cuối sidebar), dùng Tailwind CSS + component kiểu shadcn + icon Lucide (logo dùng icon, không dùng ảnh).

![Giao diện](docs/screenshot-dashboard.png)
> Ảnh chụp màn hình: thêm file `docs/screenshot-dashboard.png` sau khi chạy
> thử trên server (chụp tab Tổng quan, Bucket và File).

## Tính năng

| Tab | Làm được gì |
| --- | --- |
| **Tổng quan** | Tình trạng cluster (khỏe mạnh / suy giảm / không khả dụng), node ID/hostname/zone/dung lượng/phiên bản, tổng số bucket/key/file, thanh dung lượng đã dùng trên 40 GB đã gán. |
| **Bucket** | Liệt kê bucket kèm số file + dung lượng, tạo bucket (kiểm tra tên hợp lệ), xóa bucket (xác nhận, chỉ xóa được khi bucket rỗng), xem chi tiết bucket. |
| **Khóa** | Liệt kê key, tạo key — **Secret key chỉ hiện 1 lần** kèm nút sao chép và cảnh báo lưu lại ngay, xóa key (xác nhận), gán/thu hồi quyền Đọc / Ghi / Chủ sở hữu cho từng key trên từng bucket. |
| **File** | Duyệt file theo thư mục (tiền tố key), tìm theo tên, tải lên 1 hoặc nhiều file, tải xuống, xóa file/thư mục, xem trước ảnh trực tiếp, sao chép key của file. |
| **Bố cục** | Xem layout cluster, danh sách node, dung lượng đã gán (chỉ xem, không đổi layout ở đây để tránh làm hỏng cluster). |

Mọi thao tác đều báo thành công / lỗi bằng toast tiếng Việt.

## Kiến trúc

```
Trình duyệt  ──HTTP──▶  admin-ui (FastAPI, cổng 8000 trong container)
                          │  • Phục vụ giao diện tĩnh (app/static/)
                          │  • Proxy sang Garage Admin API bằng admin token
                          │  • Gọi S3 API bằng boto3 (khi đã có key S3)
                          ▼
                       Garage (container "garage")
                          • Admin API :3903  (Bearer admin token)
                          • S3 API    :3900  (region "garage")
```

- **Admin token không bao giờ lộ ra trình duyệt.** Trình duyệt chỉ gọi
  API của backend này (`/api/...`); backend mới là bên cầm token gọi
  Garage Admin API.
- **Admin API:** backend ưu tiên dùng **Admin API v2** (bản hiện hành trên
  Garage 2.x — xem OpenAPI `garage-admin-v2.json` của Garage), tự động
  fallback sang **v1** (deprecated từ Garage 2.0) nếu v2 trả 404/405.
  Các endpoint v2 đã kiểm chứng:
  `GET /v2/GetClusterStatus`, `GET /v2/GetClusterHealth`,
  `GET /v2/GetClusterLayout`, `GET /v2/ListBucketsInfo`,
  `GET /v2/GetBucketInfo`, `POST /v2/CreateBucket`,
  `POST /v2/DeleteBucket?id=`, `GET /v2/ListKeysInfo`,
  `GET /v2/GetKeyInfo`, `POST /v2/CreateKey`, `POST /v2/DeleteKey`,
  `POST /v2/AllowBucketKey`, `POST /v2/DenyBucketKey`.
  Lưu ý ngữ nghĩa đặc biệt của Garage: `AllowBucketKey` chỉ **bật** cờ
  = true, `DenyBucketKey` chỉ **tắt** cờ = true (cờ = false giữ nguyên),
  nên backend đọc quyền hiện tại rồi gọi Allow/Deny theo từng cờ thay đổi.
- **File (S3):** dùng boto3, `signature_version=s3v4`,
  `addressing_style=path` (Garage không có `root_domain` nên không dùng
  virtual-host style được). Cần `S3_ACCESS_KEY_ID` +
  `S3_SECRET_ACCESS_KEY`; nếu chưa có, tab File hiện hướng dẫn tạo key,
  các tab còn lại vẫn chạy bình thường (chỉ cần admin token).

## Cài đặt trên roser

Yêu cầu: Garage đã chạy bằng Docker Compose trong `/opt/garage`
(container `garage`, đã gán layout), Docker đã bật.

### 1. Sao chép code lên server

```bash
# Trên máy của bạn (hoặc trên roser, sau khi clone repo này):
scp -r garage-web-ui roser@100.85.120.39:/opt/garage/admin-ui
# Lưu ý: thư mục đích phải là /opt/garage/admin-ui (nằm trong /opt/garage
# để docker-compose.yml dưới đây trỏ build: ./admin-ui cho đúng).
```

### 2. Thêm service `admin-ui` vào `/opt/garage/docker-compose.yml`

Giữ nguyên service `garage` đã có, thêm đoạn trong file
[`compose-snippet.yml`](compose-snippet.yml) vào cuối file:

```yaml
services:
  admin-ui:
    build: ./admin-ui
    container_name: garage-admin-ui
    restart: unless-stopped
    ports:
      - "3910:8000"
    environment:
      GARAGE_ADMIN_URL: http://garage:3903
      GARAGE_ADMIN_TOKEN: ${GARAGE_ADMIN_TOKEN}
      S3_ENDPOINT_URL: http://garage:3900
      S3_REGION: garage
    depends_on:
      - garage
```

`GARAGE_ADMIN_TOKEN` lấy từ file `.env` trong `/opt/garage` (đã có sẵn
từ lúc cài Garage). **Không ghi token thật vào `docker-compose.yml`.**

### 3. Chạy lên

```bash
cd /opt/garage
docker compose up -d --build
docker compose ps
docker logs -f garage-admin-ui
```

Mở trình duyệt (đang bật Tailscale): **http://100.85.120.39:3910**

Kiểm tra nhanh: `curl http://100.85.120.39:3910/api/health`
phải trả `{"ok": true, "garage": {"connected": true, ...}}`.

### 4. Tạo bucket + key đầu tiên (bằng giao diện)

1. Tab **Bucket** → Tạo bucket, vd tên `media`.
2. Tab **Khóa** → Tạo key, vd tên `app-key` → **sao chép Secret key
   ngay** (chỉ hiện 1 lần).
3. Vẫn ở tab **Khóa**, bấm **Đổi quyền** ở dòng bucket `media` →
   tick **Đọc + Ghi** → Lưu.
4. Để dùng tab **File**, thêm 2 biến môi trường vào service `admin-ui`
   trong `/opt/garage/docker-compose.yml` (dùng Key ID + Secret vừa tạo):
   ```yaml
   environment:
     S3_ACCESS_KEY_ID: GKxxxxxxxxxxxxxxxx
     S3_SECRET_ACCESS_KEY: <secret-vua-tao>
   ```
   Rồi chạy lại: `docker compose up -d admin-ui`

## Biến môi trường

| Biến | Bắt buộc | Mặc định | Ghi chú |
| --- | --- | --- | --- |
| `GARAGE_ADMIN_URL` | Không | `http://garage:3903` | Địa chỉ Admin API (trong mạng Docker dùng tên service `garage`). |
| `GARAGE_ADMIN_TOKEN` | **Có** | — | Token quản trị Garage (`admin_token` trong `garage.toml`). Không có token thì mọi tab đều báo lỗi kết nối. |
| `S3_ENDPOINT_URL` | Không | `http://garage:3900` | Địa chỉ S3 API. |
| `S3_REGION` | Không | `garage` | Phải khớp `s3_region` trong `garage.toml`. |
| `S3_ACCESS_KEY_ID` | Không* | — | Key ID S3 để duyệt/tải file. *Bắt buộc nếu muốn dùng tab File. |
| `S3_SECRET_ACCESS_KEY` | Không* | — | Secret key S3 tương ứng. |
| `PORT` | Không | `8000` | Cổng trong container. |

## Bảo mật — đọc kỹ trước khi dùng

- **Chỉ truy cập qua Tailscale.** Không mở cổng `3910` ra internet, không
  NAT port 3910 trên router. Ai vào được giao diện này là quản trị được
  toàn bộ Garage (tạo/xóa bucket/key).
- **Admin token là chìa khóa tổng.** Nó nằm trong file `.env` của Garage
  và được truyền vào container qua biến môi trường — không ghi token thật
  vào code, không commit file `.env` (đã có trong `.gitignore`), đặt
  `chmod 600` cho `.env` và `garage.toml`.
- **Secret key S3 chỉ hiện 1 lần** khi tạo key. Mất secret thì phải xóa
  key cũ và tạo key mới — không lấy lại được.
- **Garage không có versioning.** Đẩy file trùng key sẽ ghi đè mất bản
  cũ, xóa file là mất luôn. Muốn giữ bản cũ thì đẩy với key mới
  (vd thêm ngày giờ / UUID vào tên file).
- Giao diện này **không có đăng nhập riêng**. Nếu cần nhiều người dùng,
  hãy đặt nó sau 1 lớp xác thực (vd Tailscale ACL, reverse proxy có
  basic-auth) thay vì mở trực tiếp.

## Chạy thử ở máy local (không cần server)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r app/requirements.txt
GARAGE_ADMIN_URL=http://100.85.120.39:3903 GARAGE_ADMIN_TOKEN=<token> \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
# Mở http://127.0.0.1:8001 — cần bật Tailscale để tới được roser.
```

## Gỡ bỏ

```bash
cd /opt/garage
docker compose stop admin-ui
docker compose rm -f admin-ui
# Xóa service "admin-ui" khỏi docker-compose.yml, rồi:
docker compose up -d
sudo rm -rf /opt/garage/admin-ui
```

Gỡ giao diện **không xóa dữ liệu Garage**: bucket, key và file trong
`meta/` + `data/` vẫn còn nguyên.

## Cấu trúc thư mục

```
garage-web-ui/
├── README.md               # File này
├── Dockerfile              # Python 3.12 slim, FastAPI + Uvicorn, cổng 8000
├── compose-snippet.yml     # Đoạn service admin-ui để ghép vào /opt/garage/docker-compose.yml
├── .env.example            # Mẫu biến môi trường (chỉ placeholder, không có secret thật)
├── .gitignore
└── app/
    ├── main.py             # Backend FastAPI + client Garage Admin API (v2, fallback v1) + S3 boto3
    ├── requirements.txt    # fastapi, uvicorn, httpx, boto3, python-multipart
    └── static/
        ├── index.html      # Giao diện (Tailwind + Lucide, dark/light mode)
        ├── app.js          # SPA: dashboard, bucket, key, file, layout
        └── style.css       # Component kiểu shadcn (card, nút, dialog, toast)
```

## Xử lý sự cố

| Triệu chứng | Cách xử lý |
| --- | --- |
| Giao diện báo “Không kết nối được tới Garage” | Kiểm tra `docker compose ps` trong `/opt/garage` (container `garage` phải Up), kiểm tra `GARAGE_ADMIN_TOKEN` có khớp token trong `/opt/garage/.env` không, rồi `docker compose restart admin-ui`. |
| Tab File báo “Chưa cấu hình key S3” | Làm theo hướng dẫn ở mục 4 ở trên (tạo key, gán quyền, thêm 2 biến S3 rồi `docker compose up -d admin-ui`). |
| Tạo bucket báo tên không hợp lệ | Tên 3–63 ký tự, chỉ chữ thường, số, `-` và `.`, bắt đầu/kết thúc bằng chữ hoặc số. |
| Xóa bucket báo “chưa rỗng” | Xóa hết file trong bucket ở tab File trước, rồi xóa bucket. |
| Tạo key xong không thấy Secret nữa | Secret chỉ hiện 1 lần lúc tạo. Xóa key đó và tạo key mới, lần này sao chép ngay. |
| Đổi quyền không có tác dụng | Gán quyền xong, tải lại trang (nút Làm mới). Quyền mới chỉ áp dụng cho các request S3 sau đó, key cũ đã tạo presigned URL thì URL cũ vẫn theo quyền cũ. |
