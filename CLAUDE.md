# 開發規則

- **每次修改都要先測試，測試全部通過才能 commit / push。** 執行：`tests/run.sh`
- 測試用 iPhone 尺寸（390×844）的無頭 Chrome 開 `index.html`，以模擬資料代替 Overpass 與地圖底圖，逐項檢查功能並輸出截圖到 `tests/out/`。
- 新增或改動功能時，同步在 `tests/e2e.py` 加上對應的檢查項目，並看一下截圖確認畫面。
- 回報給使用者時，列出測試結果（通過/失敗項目）與尚未能驗證的部分（例如真實 OpenStreetMap 資料）。
- 測試分兩組：A. 沒有預建資料（即時查 Overpass 的備援流程）、B. 有預建資料（`scripts/build_data.py` 用模擬資料實際產生 `data/*.json` 後載入）。
- `data/*.json` 由 GitHub Actions（`.github/workflows/build-data.yml`，使用者已同意每週自動更新）執行 `scripts/build_data.py` 產生，請勿手動編輯；`build_data.py` 的 LAYERS／BUFFER 必須與 `index.html` 一致。
- 公車／捷運路線改由 TDX 產生：`scripts/build_transit.py`（工作流程 `build-transit.yml`，GitHub Secrets 名稱 `TDX_CLIENT_ID`、`TDX_CLIENT_SECRET`）寫入 `data/routes.json`（`src: "tdx"`），請勿手動編輯；`tests/test_transit.py` 用 TDX 格式的模擬資料測試，`tests/run.sh` 會一併執行。
- 改動一律開 PR（分支 `feat/**` 或 `fix/**`），GitHub 上的 Tests 通過後直接合併（使用者同意自動合併），不需再詢問。
- 人行道類別由 `scripts/build_sidewalk.py`（工作流程 `build-sidewalk.yml`，每月）從內政部「各縣市人行道資訊」產生 `data/sidewalk.json`，只收淨寬 ≥ 1.5 公尺；app 勾選人行道時才下載。`tests/test_sidewalk.py` 用 `tests/sidewalk_mock.py` 的模擬資料測試。
