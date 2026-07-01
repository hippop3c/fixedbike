"""
雙北 YouBike 每 15 分鐘採樣一次
存成 history/YYYYMMDD-HHMM.json，只保留最近 12 小時
"""
import requests
import json
import os
import sys
import glob
from datetime import datetime, timezone, timedelta

TPE = timezone(timedelta(hours=8))
HISTORY_DIR = "history"
KEEP_HOURS = 12

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
}


def fetch_source(src):
    r = requests.get(src["url"], timeout=30, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"{src['city']} 回傳格式錯誤")
    return data


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
                merged[key]["sbi"] += int(d.get("sbi") or 0)
                merged[key]["bemp"] = max(merged[key]["bemp"], int(d.get("bemp") or 0))
                merged[key]["tot"] = max(merged[key]["tot"], int(d.get("tot") or 0))
                t = d.get("mday") or d.get("srcUpdateTime") or ""
                if t > merged[key]["mday"]:
                    merged[key]["mday"] = t
            ok_cities.append(src["city"])
        except Exception as e:
            print(f"  ✗ {src['city']} 失敗: {e}", file=sys.stderr)

    if not merged:
        print("全部來源失敗，本次不寫檔", file=sys.stderr)
        sys.exit(1)

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
