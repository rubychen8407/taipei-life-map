# taipei-life-map
taipei-life-map

臺北生活圈地圖：選你在意的設施（公園、學校、運動場所、公車站、捷運站）和步行時間，地圖直接標出「全部都走得到」的範圍，並列出推薦生活圈與各區比較。點地圖任一位置看該點到各類設施的步行時間；點公車站或捷運站，沿實際行駛路線畫出上車後會到的地方與班次密度。

線上版：https://rubychen8407.github.io/taipei-life-map/

- 生活圈：和其他類別一樣可選交通方式與時間；在地圖上點住家、公司等地點（最多 3 個），到任一地點在時間內就算符合，併入同一份分析。
- 資料：GitHub Actions 每週一執行 `scripts/build_data.py`，從 OpenStreetMap 預先下載到 `data/`（`city.json` 全市設施、`routes.json` 路線）。app 開啟時只讀 `city.json`；沒有預建資料時才即時查 Overpass。
- 步行時間以直線距離 ×1.25、每分鐘 80 公尺估算。行政區界線來自 taiwan-atlas（內政部國土測繪中心）。© OpenStreetMap 貢獻者。

## 測試

每次修改都要先跑測試：

```bash
tests/run.sh
```

用 iPhone 尺寸的無頭 Chrome 開啟頁面，以模擬資料代替 OpenStreetMap，逐項檢查載入、計算、排名、框選、錯誤重試等功能。
