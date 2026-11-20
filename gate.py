"""Decide what this run should do (no login, stdlib only).

Runs every 20 minutes. Works out the latest finished trading day (T) and
compares it with data/krx/state.json:

  run_krx   : KRX data for T still has to be fetched and KRX login is not blocked.
  krx_phase : prelim = regular-session data (out ~15:45), saved once for the 18:00 analysis
              final  = incl. after-market 16:00-20:00 (out after ~20:00-20:10), overwrites prelim
  run_naver : Naver flows for T are not final yet.

Once the final KRX data and Naver flows are saved for T, every later run stops here
without touching KRX.
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
STATE = "data/krx/state.json"
CLOSE_HHMM = "1540"        # KRX regular-session data is out by ~15:35
NAVER_FINAL_HHMM = "2030"  # Naver per-stock flows are final after ~20:18
KRX_FINAL_HHMM = "2020"    # KRX investor flows (20:00) and full short selling (20:10) are final


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def naver_days() -> list:
    url = "https://m.stock.naver.com/api/stock/003490/trend?pageSize=3"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return [str(x["bizdate"]) for x in json.load(r)]


def target_day(now: datetime) -> str:
    today = now.strftime("%Y%m%d")
    hhmm = now.strftime("%H%M")
    try:
        days = naver_days()
        if days[0] == today and hhmm < CLOSE_HHMM:
            return days[1]
        return days[0]
    except Exception:  # noqa: BLE001
        d = now if hhmm >= CLOSE_HHMM else now - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d.strftime("%Y%m%d")


def now_kst() -> datetime:
    fake = os.environ.get("FAKE_NOW_KST")  # tests only, e.g. "2026-11-23 16:07"
    if fake:
        return datetime.strptime(fake, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    return datetime.now(KST)


def main() -> None:
    now = now_kst()
    state = load_state()
    t = target_day(now)
    today = now.strftime("%Y%m%d")

    reset = os.environ.get("RESET_LOGIN", "").lower() == "true"
    if reset and state.get("login_blocked"):
        state.pop("login_blocked", None)
        save_state(state)

    blocked = bool(state.get("login_blocked"))
    krx_done = state.get("krx_day") == t  # final saved
    prelim_done = state.get("krx_prelim_day") == t
    final_ready = t < today or now.strftime("%H%M") >= KRX_FINAL_HHMM
    if krx_done:
        phase = "none"
    elif final_ready:
        phase = "final"
    elif not prelim_done:
        phase = "prelim"
    else:
        phase = "none"  # prelim saved, waiting for the final data after 20:20
    naver_done = state.get("naver_day") == t
    run_krx = phase != "none" and not blocked
    run_naver = not naver_done
    # Naver flows for T become final in the evening (or any time after T).
    naver_final = t < today or now.strftime("%H%M") >= NAVER_FINAL_HHMM

    msg = (
        f"now={now:%Y-%m-%d %H:%M} target={t} krx_final_done={krx_done} prelim_done={prelim_done} "
        f"blocked={blocked} naver_done={naver_done} -> run_krx={run_krx} phase={phase} run_naver={run_naver}"
    )
    print(msg)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"target={t}\n")
            f.write(f"run_krx={str(run_krx).lower()}\n")
            f.write(f"krx_phase={phase}\n")
            f.write(f"run_naver={str(run_naver).lower()}\n")
            f.write(f"naver_final={str(naver_final).lower()}\n")
            f.write(f"any={str(run_krx or run_naver or reset).lower()}\n")


def mark_naver(t: str) -> None:
    """After fetch_flows.py: mark Naver done for T once its flows are final."""
    import csv

    now = now_kst()
    final = t < now.strftime("%Y%m%d") or now.strftime("%H%M") >= NAVER_FINAL_HHMM
    try:
        with open("data/003490_naver_trend.csv", encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        has_t = row.get("bizdate") == t and (row.get("organPureBuyQuant") or "").strip() not in ("", "0")
    except Exception:  # noqa: BLE001
        has_t = False
    print(f"naver mark: target={t} final={final} has_t={has_t}")
    if final and has_t:
        state = load_state()
        state["naver_day"] = t
        save_state(state)


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "mark-naver":
        mark_naver(sys.argv[2])
    else:
        main()
