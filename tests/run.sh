#!/usr/bin/env bash
# 端對端測試：用 iPhone 尺寸的無頭 Chrome 開 index.html，Overpass/底圖以模擬資料代替。
set -e
cd "$(dirname "$0")"
[ -d node_modules/leaflet-polylinedecorator ] && [ -d node_modules/xlsx ] && [ -d node_modules/three ] || npm install --silent
python3 -c "import playwright" 2>/dev/null || python3 -m pip install playwright
python3 -m playwright install chromium
[ -f mock.json ] || python3 make_mock.py
python3 test_transit.py
python3 test_sidewalk.py
python3 test_realprice.py
python3 test_future.py
python3 test_trees.py
python3 test_land.py
python3 test_market.py
python3 test_urban.py
python3 e2e.py
