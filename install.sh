#!/bin/bash
# Cài Garage Web UI lên server roser (chạy trên roser, trong /opt/garage).
# Usage:  cd /opt/garage && bash admin-ui/install.sh
# Script chỉ chuẩn bị + build + chạy container, KHÔNG tạo bucket/key
# (tạo bucket/key bằng giao diện sau khi chạy xong, xem README).
set -e
cd "$(dirname "$0")/.."   # về /opt/garage

echo "== 1. Kiểm tra Docker =="
docker --version
docker compose version

echo "== 2. Kiểm tra file cần thiết =="
for f in .env garage.toml docker-compose.yml admin-ui/Dockerfile; do
  if [ ! -f "$f" ]; then echo "THIẾU file $f — xem README mục Cài đặt"; exit 1; fi
done
echo "OK: .env, garage.toml, docker-compose.yml, admin-ui/Dockerfile đều có."

echo "== 3. Build + chạy =="
docker compose up -d --build

echo "== 4. Trạng thái =="
docker compose ps
echo ""
echo "Kiểm tra sức khỏe: curl http://100.85.120.39:3910/api/health"
curl -s http://127.0.0.1:3910/api/health || true
echo ""
echo "Xong. Mở trình duyệt (bật Tailscale): http://100.85.120.39:3910"
