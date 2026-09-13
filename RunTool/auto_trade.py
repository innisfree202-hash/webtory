# -*- coding: utf-8 -*-
"""강한 매수/매도 신호 기반 자동매매 러너 (REST API, 기본 dry-run).

사용 예:
  python RunTool/auto_trade.py --paper --dry-run --once
  python RunTool/auto_trade.py --paper --live --once --budget 1000000
  python RunTool/auto_trade.py --paper --live --loop --interval 300

- 매수: 당일 강한 매수 중 점수 1등 1종목, 지정가(현재가) 또는 시장가
- 매도: 보유 중 매도신호수>=2 또는 손익률<=-8% 전량 시장가
- 안전장치: 기본 dry-run, --live 명시해야 실주문, 모의 기본, 일일 최대주문수 제한
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from Function.KoreanStockQuotes import analyze_technical_indicators
from Function.KoreanStockTrading import KISOrderClient, append_trade_log

try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None


def load_history(code: str, start: str, end: str) -> pd.DataFrame:
    hist = fdr.DataReader(code, start, end).reset_index()
    return hist.rename(columns={"Date": "일자", "Open": "시가", "High": "고가", "Low": "저가", "Close": "종가", "Volume": "거래량"})


def score_row(row: pd.Series, short_w: int, long_w: int, scan_start: str, scan_end: str):
    try:
        hist = load_history(row["Code"], scan_start, scan_end)
        if len(hist) < long_w + 5:
            return None
        ana = analyze_technical_indicators(hist, short_w, long_w)
        latest = ana.dropna(subset=["단기이동평균", "장기이동평균"]).iloc[-1]
        if not latest["강한신호"]:
            return None
        gap = (latest["단기이동평균"] - latest["장기이동평균"]) / latest["장기이동평균"]
        recent = ana.dropna(subset=["단기이동평균"]).tail(5)
        slope = (recent["단기이동평균"].iloc[-1] - recent["단기이동평균"].iloc[0]) / latest["종가"]
        score = max(0.0, 100 - abs(gap) * 1400) + max(0.0, min(30.0, slope * 1500)) + 25
        return {"code": row["Code"], "name": row["Name"], "price": float(latest["종가"]), "signal": latest["강한신호"], "score": score,
                "buy_n": int(latest["매수신호수"]), "sell_n": int(latest["매도신호수"]), "rsi": float(latest["RSI"])}
    except Exception:
        return None


def run_once(args) -> dict:
    for k in ("KIS_APP_KEY", "KIS_APP_SECRET"):
        if not os.getenv(k):
            # .env 로드
            p = PROJECT_ROOT / ".env"
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                        n, v = line.split("=", 1)
                        os.environ.setdefault(n.strip(), v.strip())
    client = KISOrderClient(is_paper=not args.real)
    scan_end = datetime.now().strftime("%Y%m%d")
    scan_start = (datetime.now() - timedelta(days=args.long * 3)).strftime("%Y%m%d")
    listing = fdr.StockListing("KRX")[["Code", "Name", "Market", "Marcap"]].dropna().sort_values("Marcap", ascending=False).head(args.universe)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    cands = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(score_row, r, args.short, args.long, scan_start, scan_end) for _, r in listing.iterrows()]
        for f in as_completed(futs):
            r = f.result()
            if r and r["signal"] == "강한 매수":
                cands.append(r)
    cands = sorted(cands, key=lambda x: x["score"], reverse=True)
    log_path = PROJECT_ROOT / "Input" / "auto_trade_log.csv"
    result: dict = {"time": datetime.now().isoformat(timespec="seconds"), "candidates": len(cands), "orders": []}

    # 매도: 실제 보유잔고 기준 (모의/실전). 잔고조회 실패 시 스킵
    try:
        bal = client.get_balance()
        # output1 컬럼: pdno, prdt_name, hldg_qty, ord_psbl_qty 등 (계좌별 상이 가능)
        for _, b in bal.iterrows():
            code = str(b.get("pdno", "")).strip()
            qty = int(float(str(b.get("hldg_qty", 0) or 0)))
            if not code or qty <= 0:
                continue
            # 해당 종목 최신 신호 확인
            try:
                hist = load_history(code, (datetime.now() - timedelta(days=365)).strftime("%Y%m%d"), scan_end)
                ana = analyze_technical_indicators(hist, args.short, args.long)
                latest = ana.dropna(subset=["단기이동평균", "장기이동평균"]).iloc[-1]
                if int(latest["매도신호수"]) >= 2:
                    o = client.order_cash(code, qty, 0, side="sell", ord_dvsn="02", dry_run=not args.live)
                    append_trade_log(log_path, {"side": "SELL", "code": code, "qty": qty, "dry_run": (not args.live), "reason": f"매도신호수={int(latest['매도신호수'])}", "resp": str(o)[:500]})
                    result["orders"].append({"side": "SELL", "code": code, "qty": qty})
                    time.sleep(0.2)
            except Exception as e:
                result.setdefault("sell_errors", []).append(f"{code}: {e}")
    except Exception as e:
        result["balance_error"] = str(e)[:300]

    # 매수: 1등만, 예산 내
    if cands and len(result["orders"]) < args.max_orders:
        top = cands[0]
        qty = int(args.budget // top["price"]) if top["price"] > 0 else 0
        if qty > 0:
            o = client.order_cash(top["code"], qty, int(top["price"]) if args.limit else 0, side="buy", ord_dvsn="01" if args.limit else "02", dry_run=not args.live)
            append_trade_log(log_path, {"side": "BUY", "code": top["code"], "name": top["name"], "qty": qty, "price": top["price"], "score": round(top["score"], 1), "dry_run": (not args.live), "resp": str(o)[:500]})
            result["orders"].append({"side": "BUY", "code": top["code"], "qty": qty, "price": top["price"]})
    print(result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="실전 URL 사용 (기본 모의)")
    ap.add_argument("--live", action="store_true", help="실주문 전송 (없으면 dry-run)")
    ap.add_argument("--once", action="store_true", help="1회 실행")
    ap.add_argument("--loop", action="store_true", help="반복 실행")
    ap.add_argument("--interval", type=int, default=300, help="반복 간격(초)")
    ap.add_argument("--budget", type=int, default=1_000_000)
    ap.add_argument("--universe", type=int, default=100)
    ap.add_argument("--short", type=int, default=20)
    ap.add_argument("--long", type=int, default=60)
    ap.add_argument("--limit", action="store_true", help="지정가 매수 (기본 시장가)")
    ap.add_argument("--max-orders", type=int, default=3)
    args = ap.parse_args()
    if args.loop:
        while True:
            try:
                run_once(args)
            except Exception as e:
                print(f"[ERROR] {e}")
            time.sleep(args.interval)
    else:
        run_once(args)


if __name__ == "__main__":
    main()