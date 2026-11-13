#!/usr/bin/env bash
# 端對端測試：用 iPhone 尺寸的無頭 Chrome 開 index.html，Overpass/底圖以模擬資料代替。
set -e
cd "$(dirname "$0")"
[ -d node_modules/leaflet ] || npm install --silent
[ -f mock.json ] || python3 make_mock.py
python3 run_e2e.py
