# taipei-life-map
taipei-life-map

臺北市最佳生活區域：勾選需要的設施（公園、綠地、學校、運動場所、公車站、捷運站）與步行分鐘數，自動找出全部都能在時間內走到的區域，並列出推薦區域排名與各區符合比例。

線上版：https://rubychen8407.github.io/taipei-life-map/

若 repo 內有 `data/*.json`（由 `scripts/build_data.py` 從 OpenStreetMap 預先下載各區設施與公車／捷運路線），app 會直接讀取、載入快；沒有時才即時查詢 Overpass。點地圖上的公車站或捷運站，會沿實際行駛路線畫出可到達的方向，線的粗細與顏色代表班距（OSM `interval` 標籤）。

步行時間以直線距離 ×1.25、每分鐘 80 公尺估算。資料來源：© OpenStreetMap 貢獻者（Overpass API）；行政區界線來自 taiwan-atlas（內政部國土測繪中心）。

## 測試

每次修改都要先跑測試：

```bash
tests/run.sh
```

用 iPhone 尺寸的無頭 Chrome 開啟頁面，以模擬資料代替 OpenStreetMap，逐項檢查載入、計算、排名、框選、錯誤重試等功能。
