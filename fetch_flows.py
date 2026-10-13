"""Daily investor-flow collector for Korean Air (003490) and the KOSPI index.

Sources (both tried; each writes its own CSV so one failing does not block the other):
  1) pykrx (KRX data portal): per-ticker net-buy volume by investor type
     (개인 / 외국인 / 기관 등) and KOSPI net-buy value by investor type.
  2) Naver Finance frgn page: daily 기관/외국인 net-buy volume, 외국인 보유율.

Outputs go to data/ ; data/status.txt records which source worked.
"""
import io
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
TICKERS = {"003490": "대한항공"}
LOOKBACK_DAYS = 60
DATA_DIR = "data"

status_lines = []


def log(msg: str) -> None:
    print(msg)
    status_lines.append(msg)


def fetch_pykrx(today: datetime) -> None:
    from pykrx import stock

    start = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    for code, name in TICKERS.items():
        try:
            vol = stock.get_market_trading_volume_by_date(start, end, code)
            val = stock.get_market_trading_value_by_date(start, end, code)
            ohlcv = stock.get_market_ohlcv_by_date(start, end, code)
            if vol is None or vol.empty:
                raise RuntimeError("empty result")
            vol = vol.add_suffix("_순매수수량")
            val = val.add_suffix("_순매수금액")
            df = ohlcv.join(vol, how="left").join(val, how="left")
            df.index.name = "날짜"
            path = f"{DATA_DIR}/{code}_pykrx.csv"
            df.to_csv(path, encoding="utf-8-sig")
            log(f"[OK] pykrx {name}({code}) rows={len(df)} last={df.index[-1].date()} -> {path}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] pykrx {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    try:
        mkt = stock.get_market_trading_value_by_date(start, end, "KOSPI")
        if mkt is None or mkt.empty:
            raise RuntimeError("empty result")
        mkt.index.name = "날짜"
        path = f"{DATA_DIR}/KOSPI_investor_value_pykrx.csv"
        mkt.to_csv(path, encoding="utf-8-sig")
        log(f"[OK] pykrx KOSPI investor value rows={len(mkt)} last={mkt.index[-1].date()} -> {path}")
    except Exception as e:  # noqa: BLE001
        log(f"[FAIL] pykrx KOSPI: {type(e).__name__}: {e}")
        traceback.print_exc()


def fetch_naver(code: str, name: str, pages: int = 3) -> None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Referer": f"https://finance.naver.com/item/main.naver?code={code}",
    }
    frames = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        html = r.content.decode("euc-kr", errors="replace")
        tables = pd.read_html(io.StringIO(html))
        target = None
        for t in tables:
            flat = " ".join(map(str, t.columns.to_flat_index()))
            if "외국인" in flat and "기관" in flat:
                target = t
        if target is None:
            raise RuntimeError(f"flow table not found on page {page}")
        target.columns = [
            "날짜", "종가", "전일비", "등락률", "거래량",
            "기관_순매매량", "외국인_순매매량", "외국인_보유주수", "외국인_보유율",
        ][: len(target.columns)]
        target = target[target["날짜"].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}")]
        frames.append(target)
    df = pd.concat(frames).drop_duplicates(subset="날짜")
    df["날짜"] = pd.to_datetime(df["날짜"], format="%Y.%m.%d")
    df = df.sort_values("날짜")
    path = f"{DATA_DIR}/{code}_naver.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] naver {name}({code}) rows={len(df)} last={df['날짜'].iloc[-1].date()} -> {path}")


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.now(KST)
    log(f"run_at_kst={today:%Y-%m-%d %H:%M}")

    try:
        fetch_pykrx(today)
    except Exception as e:  # noqa: BLE001
        log(f"[FAIL] pykrx import/setup: {type(e).__name__}: {e}")
        traceback.print_exc()

    for code, name in TICKERS.items():
        try:
            fetch_naver(code, name)
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] naver {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    with open(f"{DATA_DIR}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status_lines) + "\n")

    ok = any(line.startswith("[OK]") for line in status_lines)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
