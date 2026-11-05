"""KRX (data.krx.co.kr) collector using a logged-in session (KRX_ID / KRX_PW secrets).

Tickers come from tickers.txt (one "code,name" per line, # = comment).

Outputs (data/krx/):
  <code>_investor_daily.csv : per-stock daily net-buy VALUE by detailed investor type
  <code>_short_balance.csv  : per-stock 공매도 순보유잔고 daily trend (last ~2-3 days may be empty)
  rank_<MKT>_<INV>.csv       : net-buy ranking by value for the latest trading day (all stocks)
  short_trade_<MKT>.csv      : 공매도 거래 (all stocks) for the latest trading day
  status.txt                 : what worked
"""
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))
OUT = "data/krx"
TICKER_FILE = "tickers.txt"
status = []


def load_codes() -> dict:
    codes = {}
    try:
        with open(TICKER_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                code = parts[0].zfill(6)
                name = parts[1] if len(parts) > 1 else code
                codes[code] = name
    except FileNotFoundError:
        pass
    if not codes:
        codes = {"003490": "대한항공"}
    return codes


def log(msg: str) -> None:
    print(msg)
    status.append(msg)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(KST)
    log(f"run_at_kst={now:%Y-%m-%d %H:%M}")
    codes = load_codes()
    log(f"[OK] tickers={len(codes)}")
    if not os.environ.get("KRX_ID") or not os.environ.get("KRX_PW"):
        log("[FAIL] KRX_ID/KRX_PW not set")
        return finish()

    from krx_data_api import fetch, get_krx_auth, transport

    try:
        session = get_krx_auth().session
        log("[OK] login")
    except Exception as e:  # noqa: BLE001
        log(f"[FAIL] login: {type(e).__name__}: {e}")
        return finish()

    # code -> ISIN
    isin = {}
    try:
        listed = fetch("listed_stocks", session=session)
        isin = dict(zip(listed["ISU_SRT_CD"].astype(str), listed["ISU_CD"].astype(str)))
        log(f"[OK] listed_stocks rows={len(listed)}")
    except Exception as e:  # noqa: BLE001
        log(f"[FAIL] listed_stocks: {type(e).__name__}: {e}")

    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=90)).strftime("%Y%m%d")
    last_trade_day = None

    # 1) per-stock detailed investor flows
    for code, name in codes.items():
        try:
            if code not in isin:
                raise RuntimeError("ISIN not found")
            df = fetch(
                "investor_trading_individual_daily_json",
                session=session,
                isuCd=isin[code],
                strtDd=start,
                endDd=end,
            )
            if df.empty:
                raise RuntimeError("empty")
            df.insert(0, "name", name)
            df.insert(0, "code", code)
            df.to_csv(f"{OUT}/{code}_investor_daily.csv", index=False, encoding="utf-8-sig")
            first_col = [c for c in df.columns if "DD" in c.upper()] or [df.columns[2]]
            d0 = str(df[first_col[0]].iloc[0])
            last_trade_day = last_trade_day or d0
            log(f"[OK] {name}({code}) rows={len(df)} first_row_date={d0}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    day = (last_trade_day or end).replace("/", "").replace("-", "")

    # 2) net-buy rankings (all stocks) for latest trading day
    for mkt in ["STK", "KSQ"]:
        for inv, inv_name in [("9000", "foreign"), ("7050", "inst"), ("6000", "pension")]:
            tag = f"{mkt}_{inv_name}"
            try:
                payload = transport.json_data(
                    "dbms/MDC/STAT/standard/MDCSTAT02401",
                    {
                        "mktId": mkt,
                        "invstTpCd": inv,
                        "strtDd": day,
                        "endDd": day,
                        "share": "1",
                        "money": "1",
                        "csvxls_isNo": "false",
                    },
                    session=session,
                    menu_id="MDC0201020303",
                )
                rows = payload.get("output") or payload.get("OutBlock_1") or []
                if not rows:
                    raise RuntimeError(f"no rows; keys={list(payload)[:6]}")
                df = pd.DataFrame(rows)
                df.to_csv(f"{OUT}/rank_{tag}.csv", index=False, encoding="utf-8-sig")
                log(f"[OK] rank {tag} day={day} rows={len(df)}")
            except Exception as e:  # noqa: BLE001
                log(f"[FAIL] rank {tag}: {type(e).__name__}: {e}")

    # 3) 공매도 거래 (all stocks) for latest trading day
    for mkt in ["STK", "KSQ"]:
        try:
            df = fetch("short_selling_individual", session=session, trdDd=day, mktId=mkt)
            if df.empty:
                raise RuntimeError("empty")
            df.to_csv(f"{OUT}/short_trade_{mkt}.csv", index=False, encoding="utf-8-sig")
            log(f"[OK] short_trade {mkt} day={day} rows={len(df)} cols={list(df.columns)[:12]}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] short_trade {mkt}: {type(e).__name__}: {e}")

    # 4) 공매도 순보유잔고 trend per ticker (last ~90 days)
    #    KRX blocks long bursts on one session -> pace requests, re-login + retry on failure
    relogins = 0
    for code, name in codes.items():
        try:
            if code not in isin:
                raise RuntimeError("ISIN not found")
            df = None
            for attempt in range(3):
                try:
                    time.sleep(0.4)
                    df = fetch(
                        "short_balance_individual_trend",
                        session=session,
                        isuCd=isin[code],
                        strtDd=start,
                        endDd=end,
                    )
                    break
                except Exception:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    time.sleep(3 + attempt * 3)
                    try:
                        auth = get_krx_auth()
                        auth.invalidate()  # singleton caches the session -> force a fresh login
                        session = auth.session
                        relogins += 1
                    except Exception:  # noqa: BLE001
                        pass
            if df.empty:
                raise RuntimeError("empty")
            df.insert(0, "name", name)
            df.insert(0, "code", code)
            df.to_csv(f"{OUT}/{code}_short_balance.csv", index=False, encoding="utf-8-sig")
            log(f"[OK] short_balance {name}({code}) rows={len(df)} cols={list(df.columns)[:10]}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] short_balance {name}({code}): {type(e).__name__}: {e}")
    log(f"short_balance relogins={relogins}")

    return finish()


def finish() -> int:
    with open(f"{OUT}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
