"""Daily investor-flow collector (Naver mobile stock API).

For each ticker: daily 개인/외국인/기관 net-buy volume, close, volume, 외국인 보유율
for the last ~60 trading days -> data/<code>_naver_trend.csv (+ raw JSON).
Plus ~1 year of daily OHLCV (Naver chart feed) -> data/<code>_ohlcv.csv.
data/status.txt records what worked.
"""
import glob
import json
import os
import re
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
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "006400": "삼성SDI",
    "393890": "더블유씨피",
    "361610": "SK아이이테크놀로지",
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


def fetch_ohlcv(code: str, name: str, count: int = 300) -> None:
    url = (
        "https://fchart.stock.naver.com/sise.nhn"
        f"?symbol={code}&timeframe=day&count={count}&requestType=0"
    )
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    text = r.content.decode("euc-kr", errors="replace")
    items = re.findall(r'data="([^"]+)"', text)
    if not items:
        with open(f"{DATA_DIR}/{code}_ohlcv_raw.txt", "w", encoding="utf-8") as f:
            f.write(text[:5000])
        raise RuntimeError("no items in chart feed")
    rows = []
    for it in items:
        parts = it.split("|")
        if len(parts) >= 6:
            rows.append(parts[:6])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date")
    path = f"{DATA_DIR}/{code}_ohlcv.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] ohlcv {name}({code}) rows={len(df)} {df['date'].iloc[0]}~{df['date'].iloc[-1]} -> {path}")


def fetch_deal_rank() -> None:
    """Naver 외국인/기관 순매수·순매도 상위 (금액 기준) for KOSPI/KOSDAQ."""
    from lxml import html as lxml_html
    markets = {"01": "KOSPI", "02": "KOSDAQ"}
    whos = {"9000": "foreign", "1000": "inst"}
    for sosok, mname in markets.items():
        for gubun, wname in whos.items():
            for side in ["buy", "sell"]:
                url = (
                    "https://finance.naver.com/sise/sise_deal_rank_iframe.naver"
                    f"?sosok={sosok}&investor_gubun={gubun}&type={side}"
                )
                tag = f"{mname}_{wname}_{side}"
                try:
                    r = requests.get(url, headers=UA, timeout=20)
                    r.raise_for_status()
                    text = r.content.decode("euc-kr", errors="replace")
                    doc = lxml_html.fromstring(text)
                    rows = []
                    for tr in doc.xpath("//tr"):
                        a = tr.xpath(".//a")
                        tds = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.xpath("./td")]
                        if a and len(tds) >= 3:
                            name = a[0].text_content().strip()
                            href = a[0].get("href", "")
                            m = re.search(r"code=(\d{6})", href)
                            nums = [t for t in tds if re.fullmatch(r"[-+]?[\d,]+", t)]
                            rows.append([name, m.group(1) if m else ""] + nums)
                    dates = re.findall(r"\d{2,4}\.\d{2}\.\d{2}", text)
                    if not rows:
                        with open(f"{DATA_DIR}/rank_{tag}_raw.html", "w", encoding="utf-8") as f:
                            f.write(text)
                        raise RuntimeError("no rows")
                    width = max(len(x) for x in rows)
                    cols = ["name", "code", "qty", "amount", "volume"][:width]
                    cols += [f"c{i}" for i in range(len(cols), width)]
                    df = pd.DataFrame([x + [""] * (width - len(x)) for x in rows], columns=cols)
                    path = f"{DATA_DIR}/rank_{tag}.csv"
                    df.to_csv(path, index=False, encoding="utf-8-sig")
                    log(f"[OK] rank {tag} rows={len(df)} date={dates[:1]} -> {path}")
                except Exception as e:  # noqa: BLE001
                    log(f"[FAIL] rank {tag}: {type(e).__name__}: {e}")


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
        try:
            fetch_ohlcv(code, name)
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] ohlcv {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    fetch_deal_rank()

    with open(f"{DATA_DIR}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status_lines) + "\n")
    ok = any(line.startswith("[OK]") for line in status_lines)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
