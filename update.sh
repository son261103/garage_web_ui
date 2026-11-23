#!/bin/bash
# Cập nhật cả Garage + Garage Web UI rồi chạy lại — chỉ cần 1 lệnh.
# Update both Garage and the Web UI, then restart — one command.
#
# Cách dùng trên roser (trong /opt/garage):
#   bash admin-ui/update.sh
#
# Script làm 4 việc:
#   1. Lấy code giao diện mới nhất từ GitHub (git pull trong admin-ui)
#   2. Tải image Garage mới nhất (docker compose pull garage)
#   3. Build lại giao diện + chạy lại cả 2 container (up -d --build)
#   4. In trạng thái + kiểm tra sức khỏe (/api/health)
#
# An toàn: KHÔNG xóa dữ liệu. meta/ và data/ là thư mục trên máy,
# docker compose up chỉ tạo lại container, không động tới 2 thư mục đó.
# Nếu chỉ muốn cập nhật giao diện (không động Garage), chạy:
#   bash admin-ui/update.sh --ui-only
set -e
cd "$(dirname "$0")/.."   # về /opt/garage

UI_ONLY=0
[ "${1:-}" = "--ui-only" ] && UI_ONLY=1

echo "== 1. Cập nhật code giao diện từ GitHub =="
cd admin-ui && git pull && cd ..

if [ "$UI_ONLY" = 0 ]; then
  echo "== 2. Tải image Garage mới nhất =="
  docker compose pull garage
else
  echo "== 2. Bỏ qua Garage (--ui-only) =="
fi

echo "== 3. Build lại giao diện + chạy lại cả 2 container =="
docker compose up -d --build

echo "== 4. Trạng thái =="
docker compose ps
echo ""
echo "== 5. Kiểm tra sức khỏe =="
sleep 3
curl -s http://127.0.0.1:3910/api/health || echo "Chưa gọi được /api/health, xem log: docker logs garage-admin-ui"
echo ""
echo "Xong. Mở: http://100.85.120.39:3910"
