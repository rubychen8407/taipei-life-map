# 開發規則

- **每次修改都要先測試，測試全部通過才能 commit / push。** 執行：`tests/run.sh`
- 測試用 iPhone 尺寸（390×844）的無頭 Chrome 開 `index.html`，以模擬資料代替 Overpass 與地圖底圖，逐項檢查功能並輸出截圖到 `tests/out/`。
- 新增或改動功能時，同步在 `tests/run_e2e.py` 加上對應的檢查項目，並看一下截圖確認畫面。
- 回報給使用者時，列出測試結果（通過/失敗項目）與尚未能驗證的部分（例如真實 OpenStreetMap 資料）。
