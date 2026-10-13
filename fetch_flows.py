"""Daily investor-flow collector for Korean Air (003490) and the KOSPI market.

Sources (each independent; one failing does not block the others):
  1) Naver mobile API  : per-stock daily 개인/외국인/기관 net-buy volume (JSON, raw saved too)
  2) Naver frgn page   : per-stock daily 기관/외국인 net-buy volume, 외국인 보유율
  3) Naver market page : KOSPI daily investor net-buy value (억원)
  4) pykrx (KRX)       : per-stock and KOSPI investor flows (may need KRX login)

Outputs go to data/ ; data/status.txt records what worked.
"""
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from lxml import html as lxml_html

KST = timezone(timedelta(hours=9))
TICKERS = {"003490": "대한항공"}
LOOKBACK_DAYS = 60
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


def to_num(s: str):
    s = s.replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return s


# 1) Naver mobile API -------------------------------------------------------
def fetch_naver_trend(code: str, name: str) -> None:
    url = f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60"
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    with open(f"{DATA_DIR}/{code}_naver_trend_raw.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    rows = data if isinstance(data, list) else data.get("result") or data.get("items") or []
    if not rows:
        raise RuntimeError(f"no rows; top-level keys={list(data)[:10] if isinstance(data, dict) else type(data)}")
    df = pd.DataFrame(rows)
    path = f"{DATA_DIR}/{code}_naver_trend.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] naver-trend {name}({code}) rows={len(df)} cols={list(df.columns)[:12]} -> {path}")


# 2) Naver frgn page --------------------------------------------------------
def fetch_naver_frgn(code: str, name: str, pages: int = 3) -> None:
    headers = dict(UA, Referer=f"https://finance.naver.com/item/main.naver?code={code}")
    out = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        text = r.content.decode("euc-kr", errors="replace")
        if page == 1:
            with open(f"{DATA_DIR}/debug_{code}_frgn_p1.html", "w", encoding="utf-8") as f:
                f.write(text)
        doc = lxml_html.fromstring(text)
        for tr in doc.xpath("//table//tr"):
            tds = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.xpath("./td")]
            if len(tds) >= 9 and re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", tds[0]):
                out.append(tds[:9])
    if not out:
        raise RuntimeError("no rows parsed")
    cols = ["날짜", "종가", "전일비", "등락률", "거래량",
            "기관_순매매량", "외국인_순매매량", "외국인_보유주수", "외국인_보유율"]
    df = pd.DataFrame(out, columns=cols).drop_duplicates(subset="날짜")
    for c in cols[1:]:
        if c != "전일비":
            df[c] = df[c].map(to_num)
    df["날짜"] = pd.to_datetime(df["날짜"], format="%Y.%m.%d")
    df = df.sort_values("날짜")
    path = f"{DATA_DIR}/{code}_naver_frgn.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] naver-frgn {name}({code}) rows={len(df)} last={df['날짜'].iloc[-1].date()} -> {path}")


# 3) Naver KOSPI investor flows ---------------------------------------------
def fetch_naver_market(today: datetime, pages: int = 3) -> None:
    bizdate = today.strftime("%Y%m%d")
    out = []
    for page in range(1, pages + 1):
        url = (
            "https://finance.naver.com/sise/investorDealTrendDay.naver"
            f"?bizdate={bizdate}&sosok=01&page={page}"
        )
        r = requests.get(url, headers=UA, timeout=20)
        r.raise_for_status()
        text = r.content.decode("euc-kr", errors="replace")
        if page == 1:
            with open(f"{DATA_DIR}/debug_kospi_investor_p1.html", "w", encoding="utf-8") as f:
                f.write(text)
        doc = lxml_html.fromstring(text)
        for tr in doc.xpath("//table//tr"):
            tds = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.xpath("./td")]
            if len(tds) >= 4 and re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", tds[0]):
                out.append(tds)
    if not out:
        raise RuntimeError("no rows parsed")
    width = max(len(r) for r in out)
    cols = ["날짜", "개인", "외국인", "기관계", "금융투자", "보험", "투신",
            "은행", "기타금융", "연기금등", "기타법인"][:width]
    cols += [f"col{i}" for i in range(len(cols), width)]
    df = pd.DataFrame([r + [""] * (width - len(r)) for r in out], columns=cols)
    df = df.drop_duplicates(subset="날짜")
    for c in cols[1:]:
        df[c] = df[c].map(to_num)
    df["날짜"] = pd.to_datetime(df["날짜"], format="%y.%m.%d")
    df = df.sort_values("날짜")
    path = f"{DATA_DIR}/KOSPI_investor_naver_억원.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"[OK] naver-market KOSPI rows={len(df)} last={df['날짜'].iloc[-1].date()} -> {path}")


# 4) pykrx ------------------------------------------------------------------
def fetch_pykrx(today: datetime) -> None:
    import pykrx
    from pykrx import stock

    log(f"pykrx version={getattr(pykrx, '__version__', '?')}")
    start = (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    for code, name in TICKERS.items():
        try:
            vol = stock.get_market_trading_volume_by_date(start, end, code)
            if vol is None or vol.empty:
                raise RuntimeError("empty result (KRX may require login)")
            val = stock.get_market_trading_value_by_date(start, end, code)
            df = vol.add_suffix("_순매수수량").join(val.add_suffix("_순매수금액"), how="left")
            df.index.name = "날짜"
            path = f"{DATA_DIR}/{code}_pykrx.csv"
            df.to_csv(path, encoding="utf-8-sig")
            log(f"[OK] pykrx {name}({code}) rows={len(df)} last={df.index[-1].date()} -> {path}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] pykrx {name}({code}): {type(e).__name__}: {e}")


def run(label: str, fn, *args) -> None:
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        log(f"[FAIL] {label}: {type(e).__name__}: {e}")
        traceback.print_exc()


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    today = datetime.now(KST)
    log(f"run_at_kst={today:%Y-%m-%d %H:%M}")
    for code, name in TICKERS.items():
        run(f"naver-trend {name}({code})", fetch_naver_trend, code, name)
        run(f"naver-frgn {name}({code})", fetch_naver_frgn, code, name)
    run("naver-market KOSPI", fetch_naver_market, today)
    run("pykrx", fetch_pykrx, today)

    with open(f"{DATA_DIR}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status_lines) + "\n")
    ok = any(line.startswith("[OK]") for line in status_lines)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
