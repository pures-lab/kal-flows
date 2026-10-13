"""Daily investor-flow collector (Naver mobile stock API).

For each ticker: daily 개인/외국인/기관 net-buy volume, close, volume, 외국인 보유율
for the last ~60 trading days -> data/<code>_naver_trend.csv (+ raw JSON).
data/status.txt records what worked.
"""
import glob
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
TICKERS = {
    "003490": "대한항공",
    "035420": "NAVER",
    "036570": "엔씨소프트",
    "352820": "하이브",
    "009830": "한화솔루션",
}
DATA_DIR = "data"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

status_lines = []


def log(msg: str) -> None:
    print(msg)
    status_lines.append(msg)


def fetch_naver_trend(code: str, name: str) -> None:
    url = f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    with open(f"{DATA_DIR}/{code}_naver_trend_raw.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    rows = data if isinstance(data, list) else data.get("result") or data.get("items") or []
    if not rows:
        raise RuntimeError("no rows")
    df = pd.DataFrame(rows)
    df.insert(1, "name", name)
    path = f"{DATA_DIR}/{code}_naver_trend.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] {name}({code}) rows={len(df)} last={df['bizdate'].iloc[0]} -> {path}")


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    for old in glob.glob(f"{DATA_DIR}/debug_*.html"):
        os.remove(old)
    today = datetime.now(KST)
    log(f"run_at_kst={today:%Y-%m-%d %H:%M}")
    for code, name in TICKERS.items():
        try:
            fetch_naver_trend(code, name)
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    with open(f"{DATA_DIR}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status_lines) + "\n")
    ok = any(line.startswith("[OK]") for line in status_lines)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
