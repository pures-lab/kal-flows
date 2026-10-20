"""KRX (data.krx.co.kr) collector using a logged-in session (KRX_ID / KRX_PW secrets).

Outputs (data/krx/):
  <code>_investor_daily.csv : per-stock daily net-buy VALUE by detailed investor type
  rank_<MKT>_<INV>.csv       : top net-buy/net-sell stocks by value for the latest trading day
  status.txt                 : what worked
"""
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd

KST = timezone(timedelta(hours=9))
CODES = {
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
OUT = "data/krx"
status = []


def log(msg: str) -> None:
    print(msg)
    status.append(msg)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(KST)
    log(f"run_at_kst={now:%Y-%m-%d %H:%M}")
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
    for code, name in CODES.items():
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
            log(f"[OK] {name}({code}) rows={len(df)} first_row_date={d0} cols={list(df.columns)[:14]}")
        except Exception as e:  # noqa: BLE001
            log(f"[FAIL] {name}({code}): {type(e).__name__}: {e}")
            traceback.print_exc()

    # net-buy rankings (value) for latest trading day
    day = (last_trade_day or end).replace("/", "").replace("-", "")
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
                log(f"[OK] rank {tag} day={day} rows={len(df)} cols={list(df.columns)[:10]}")
            except Exception as e:  # noqa: BLE001
                log(f"[FAIL] rank {tag}: {type(e).__name__}: {e}")
    return finish()


def finish() -> int:
    with open(f"{OUT}/status.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(status) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
