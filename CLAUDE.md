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
- 中古屋估價模組（PRD：https://claude.ai/artifact/D9dMHpPZK7yg4NfgDMjdx4）：`scripts/build_realprice.py`（工作流程 `build-realprice.yml`，每月 2、12、22 日）從內政部實價登錄＋臺北市門牌位置數值資料產生 `data/realprice.json`、`data/valuation.json`，請勿手動編輯；回測 MdAPE 超過 12% 時建置失敗、不更新。估價公式（`estimate()`、`base_x()`、`quantile()`、生活機能分數）在 Python 與 `index.html` 各有一份，改一邊要同步改另一邊。`config/finance.json`（利率、成數、議價率）是手動維護，每個數字要附來源。`tests/test_realprice.py` 用 `tests/realprice_mock.py` 的模擬資料測試。
- 未來捷運：`scripts/build_future.py`（在 `build-data.yml` 每週跑）從 OpenStreetMap 抓興建中／規劃中的捷運與輕軌，寫入 `data/future.json`；只當資訊顯示（地圖圖層、估價位置分頁、比較表），不計入估價。`tests/test_future.py` 用 `tests/future_mock.py` 測試。
- 行道樹：`scripts/build_trees.py`（工作流程 `build-trees.yml`，每月）從臺北市公園處「行道樹分布圖」（data.gov.tw 128274，TaipeiTree.csv，TWD97）產生 `data/trees.json`（壓縮格式，每棵 6 欄，`FIELDS` 與 `index.html` 的 `TREE_F` 一致）；圖層「環境 › 行道樹」預設關，勾選才下載，縮放 ≥ 15 才畫。`tests/test_trees.py` 用 `tests/trees_mock.py` 測試。
- 土地價值：`scripts/build_land.py`（工作流程 `build-land.yml`，每月）用臺北市數值地籍地段圖（157685）＋土地公告現值（122058，取最新一年）＋各區漲跌幅（132106）＋公有土地當期現值（128228，備援）產生 `data/land.json`：每個小段的公告現值中位數；圖層「房市 › 土地價值」預設關、勾選才下載，和生活機能熱圖、單價熱圖一次只開一種（`FILLS`）。`tests/test_land.py` 用 `tests/land_mock.py`（自己寫出 SHP/DBF）測試。
