"""
雙北 YouBike 每 15 分鐘採樣一次
存成 history/YYYYMMDD-HHMM.json，只保留最近 12 小時
"""
import requests
import json
import os
import sys
import glob
import time
from datetime import datetime, timezone, timedelta

TPE = timezone(timedelta(hours=8))
HISTORY_DIR = "history"
KEEP_HOURS = 12

OFFICIAL_REALTIME_URL = "https://apis.youbike.com.tw/tw2/parkingInfo"
# 11 個 10 公里查詢圈可覆蓋目前雙北全部站點；每次採樣僅 11 次請求。
OFFICIAL_AREA_CENTERS = [
    (25.03915, 121.52163),
    (25.00557, 121.41306),
    (25.18696, 121.44407),
    (25.01066, 121.67716),
    (24.93192, 121.40753),
    (25.0590226, 121.8518644),
    (25.21974, 121.62916),
    (24.95091, 121.5474),
    (25.25232, 121.47131),
    (25.10893, 121.46639),
    (25.10113, 121.55159),
]

SOURCES = [
    {
        "city": "新北市",
        "prefix": "NTPC-",
        "url": "https://green-boat-a984.hippop3c.workers.dev/",
    },
    {
        "city": "臺北市",
        "prefix": "TPE-",
        "url": "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; youbike-sampler/1.0)",
    "Accept": "application/json",
    "Cache-Control": "no-cache, no-store",
}


def fetch_source(src):
    r = requests.get(src["url"], timeout=30, headers=HEADERS, params={"_": int(time.time())})
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"{src['city']} 回傳格式錯誤")
    return data


def fetch_official_realtime():
    """讀取 YouBike 官網地圖使用的即時站位資料，並依站碼去重。"""
    live = {}
    for lat, lng in OFFICIAL_AREA_CENTERS:
        r = requests.post(
            OFFICIAL_REALTIME_URL,
            json={"lat": lat, "lng": lng, "maxDistance": 10000},
            timeout=30,
            headers=HEADERS,
            params={"_": int(time.time())},
        )
        r.raise_for_status()
        payload = r.json()
        if not payload.get("retCode") or not isinstance(payload.get("retVal"), list):
            raise RuntimeError(payload.get("retMsg") or "官網即時資料格式錯誤")
        for station in payload["retVal"]:
            sno = str(station.get("station_no") or "")
            if sno.startswith(("5001", "5002")):
                live[sno] = station
    return live


def main():
    now = datetime.now(TPE)
    ts = now.strftime("%Y%m%d-%H%M")
    print(f"=== 採樣時間：{now.strftime('%Y-%m-%d %H:%M:%S')} ===")

    # 合併：同站的 2.0 / 2.0E 相加
    merged = {}
    ok_cities = []
    for src in SOURCES:
        try:
            raw = fetch_source(src)
            print(f"  ✓ {src['city']}: {len(raw)} 筆")
            for d in raw:
                sno = d.get("sno")
                if not sno:
                    continue
                key = src["prefix"] + str(sno)
                sna = (d.get("sna") or "").replace("YouBike2.0_", "")
                if key not in merged:
                    merged[key] = {
                        "sno": key,
                        "sna": sna,
                        "sarea": d.get("sarea", ""),
                        "city": src["city"],
                        "sbi": 0,
                        "bemp": 0,
                        "tot": 0,
                        "mday": "",
                    }
                sbi = d.get("sbi", d.get("available_rent_bikes", d.get("availableRentBikes", 0)))
                bemp = d.get("bemp", d.get("available_return_bikes", d.get("availableReturnBikes", 0)))
                tot = d.get(
                    "Quantity",
                    d.get("quantity", d.get("tot_quantity", d.get("tot", d.get("total", d.get("totalDocks", 0))))),
                )
                merged[key]["sbi"] += int(sbi or 0)
                merged[key]["bemp"] = max(merged[key]["bemp"], int(bemp or 0))
                merged[key]["tot"] = max(merged[key]["tot"], int(tot or 0))
                t = d.get("mday") or d.get("srcUpdateTime") or ""
                if t > merged[key]["mday"]:
                    merged[key]["mday"] = t
            ok_cities.append(src["city"])
        except Exception as e:
            print(f"  ✗ {src['city']} 失敗: {e}", file=sys.stderr)

    if not merged:
        print("全部來源失敗，本次不寫檔", file=sys.stderr)
        sys.exit(1)

    # 開放資料只作站點清單與備援；數量以 YouBike 官網地圖的即時端點覆寫。
    try:
        official_live = fetch_official_realtime()
        updated = 0
        for key, station in merged.items():
            raw_sno = key.split("-", 1)[-1]
            live = official_live.get(raw_sno)
            if not live:
                continue
            station["sbi"] = int(live.get("available_spaces") or 0)
            station["bemp"] = int(live.get("empty_spaces") or 0)
            station["tot"] = int(live.get("parking_spaces") or 0)
            station["state_signature"] = "{}:{}:{}".format(
                station["sbi"], station["bemp"], live.get("status")
            )
            updated += 1
        print(f"  ✓ YouBike 官網即時站位: {updated} 站")
    except Exception as e:
        print(f"  ⚠ 官網即時站位失敗，沿用開放資料備援: {e}", file=sys.stderr)

    # 精簡：分析階段只需要 sbi 和 mday，其它欄位靠最新一次採樣就好
    payload = {
        "sampled_at": now.isoformat(timespec="seconds"),
        "cities": ok_cities,
        "stations": {
            key: {
                "sna": s["sna"],
                "sarea": s["sarea"],
                "city": s["city"],
                "sbi": s["sbi"],
                "bemp": s["bemp"],
                "tot": s["tot"],
                "mday": s["mday"],
                "state_signature": s.get("state_signature", s["mday"]),
            }
            for key, s in merged.items()
        },
    }

    os.makedirs(HISTORY_DIR, exist_ok=True)
    out_path = os.path.join(HISTORY_DIR, f"{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"✓ 已寫入 {out_path}（{len(merged)} 站）")

    # 清舊：超過 KEEP_HOURS 的檔案刪掉
    cutoff = now - timedelta(hours=KEEP_HOURS)
    removed = 0
    for f in glob.glob(os.path.join(HISTORY_DIR, "*.json")):
        name = os.path.basename(f).replace(".json", "")
        try:
            file_time = datetime.strptime(name, "%Y%m%d-%H%M").replace(tzinfo=TPE)
        except ValueError:
            continue
        if file_time < cutoff:
            os.remove(f)
            removed += 1
    if removed:
        print(f"✓ 已清除 {removed} 個過期檔案（超過 {KEEP_HOURS} 小時）")


if __name__ == "__main__":
    main()
