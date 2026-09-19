# -*- coding: utf-8 -*-
"""강한 매수/매도 신호 알림 러너 (자동주문 금지, 팝업 확인 방식).

사용 예:
  python RunTool/auto_trade.py --once
  python RunTool/auto_trade.py --live --once --budget 1000000
  python RunTool/auto_trade.py --live --loop --interval 300

- 매수: 정규시장 시간(평일 09:00~15:30 KST)에만, 강한 매수 중 점수 상위 최대 3종목/일,
  회당 최대 1,000,000원, 포트폴리오 전체 매수가 20,000,000원 초과 금지,
  정규시장 현시간가로 지정가 매수 제안 → 팝업 확인 후에만 주문
- 매도: 보유 중 매도신호수>=3 전량 시장가 → 팝업 확인 후에만 주문
- 안전장치: 기본 dry-run, --live + 팝업 확인 시에만 실주문, 모의 기본
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from Function.KoreanStockQuotes import KoreanStockQuoteClient, analyze_technical_indicators
from Function.KoreanStockTrading import KISOrderClient, append_trade_log


def is_regular_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) < (15, 30)


def get_live_price(code: str) -> float | None:
    try:
        qc = KoreanStockQuoteClient()
        qc.get_current_order_book(code)
        if qc.last_current_price is not None:
            return float(qc.last_current_price)
    except Exception:
        pass
    return None


def popup_confirm(title: str, message: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        ans = messagebox.askyesno(title, message)
        root.destroy()
        return bool(ans)
    except Exception:
        try:
            ans = input(f"[{title}] {message} (y/N): ").strip().lower()
            return ans in ("y", "yes", "예")
        except Exception:
            return False


def get_today_buy_count(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    try:
        df = pd.read_csv(log_path, encoding="utf-8-sig")
        today = datetime.now().strftime("%Y-%m-%d")
        mask = df["time"].astype(str).str.startswith(today) if "time" in df.columns else False
        sides = df["side"].astype(str) if "side" in df.columns else pd.Series([], dtype=str)
        return int(((sides == "BUY") & mask).sum())
    except Exception:
        return 0


def get_portfolio_cost(client: KISOrderClient) -> float:
    try:
        bal = client.get_balance()
        total = 0.0
        for _, b in bal.iterrows():
            try:
                qty = float(str(b.get("hldg_qty", 0) or 0))
                avg = float(str(b.get("pchs_avg_pric", b.get("avg_prvs", 0)) or 0))
                total += qty * avg
            except Exception:
                continue
        return total
    except Exception:
        return 0.0

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
                if int(latest["매도신호수"]) >= 3:
                    msg = f"[{code}] 매도신호수 {int(latest['매도신호수'])}개 → 보유 {qty}주 시장가 매도할까요?"
                    ok = popup_confirm("매도 확인", msg) if args.popup else False
                    if args.live and ok:
                        o = client.order_cash(code, qty, 0, side="sell", ord_dvsn="02", dry_run=False)
                        append_trade_log(log_path, {"side": "SELL", "code": code, "qty": qty, "dry_run": False, "reason": f"매도신호수={int(latest['매도신호수'])} 팝업확인", "resp": str(o)[:500]})
                        result["orders"].append({"side": "SELL", "code": code, "qty": qty})
                    else:
                        append_trade_log(log_path, {"side": "SELL_PROPOSE", "code": code, "qty": qty, "dry_run": True, "reason": f"매도신호수={int(latest['매도신호수'])} 팝업대기/거절", "resp": ""})
                        result.setdefault("skipped", []).append({"side": "SELL", "code": code, "reason": "popup not confirmed"})
                    time.sleep(0.2)
            except Exception as e:
                result.setdefault("sell_errors", []).append(f"{code}: {e}")
    except Exception as e:
        result["balance_error"] = str(e)[:300]

    # 매수: 정규장 시간에만, 회당 100만원, 포트폴리오 2000만원, 일 3회 한도, 현시간가 지정가, 팝업 확인
    result.setdefault("skipped", [])
    market_open = is_regular_market_open()
    result["market_open"] = market_open
    if not market_open:
        result["skipped"].append({"side": "BUY", "reason": "정규시장 시간 아님(평일 09:00~15:30만 매수)"})
    else:
        today_buys = get_today_buy_count(log_path)
        portfolio_cost = get_portfolio_cost(client)
        result["today_buys"] = today_buys
        result["portfolio_cost"] = portfolio_cost
        per_buy_cap = min(args.budget, 1_000_000)
        for top in cands[: max(0, args.max_daily_buys - today_buys)]:
            if today_buys >= args.max_daily_buys:
                result["skipped"].append({"side": "BUY", "reason": f"일일 최대 {args.max_daily_buys}회 초과"})
                break
            if portfolio_cost >= args.max_portfolio:
                result["skipped"].append({"side": "BUY", "reason": f"포트폴리오 {args.max_portfolio:,}원 초과"})
                break
            live = get_live_price(top["code"])
            if live is None:
                result["skipped"].append({"side": "BUY", "code": top["code"], "reason": "현시간가 조회 실패"})
                continue
            budget_left = min(per_buy_cap, args.max_portfolio - portfolio_cost)
            qty = int(budget_left // live) if live > 0 else 0
            if qty <= 0:
                result["skipped"].append({"side": "BUY", "code": top["code"], "reason": "예산 부족"})
                continue
            msg = f"[{top['code']} {top['name']}] 점수 {top['score']:.1f}, 현시간가 {live:,.0f}원 x {qty}주 지정가 매수할까요? (금일 {today_buys+1}/{args.max_daily_buys})"
            ok = popup_confirm("매수 확인", msg) if args.popup else False
            if args.live and ok:
                o = client.order_cash(top["code"], qty, int(live), side="buy", ord_dvsn="01", dry_run=False)
                append_trade_log(log_path, {"side": "BUY", "code": top["code"], "name": top["name"], "qty": qty, "price": live, "score": round(top["score"], 1), "dry_run": False, "resp": str(o)[:500]})
                result["orders"].append({"side": "BUY", "code": top["code"], "qty": qty, "price": live})
                today_buys += 1
                portfolio_cost += qty * live
            else:
                append_trade_log(log_path, {"side": "BUY_PROPOSE", "code": top["code"], "name": top["name"], "qty": qty, "price": live, "score": round(top["score"], 1), "dry_run": True, "resp": ""})
                result["skipped"].append({"side": "BUY", "code": top["code"], "reason": "popup not confirmed"})
    print(result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="실전 URL 사용 (기본 모의)")
    ap.add_argument("--live", action="store_true", help="실주문 전송 (없으면 dry-run)")
    ap.add_argument("--once", action="store_true", help="1회 실행")
    ap.add_argument("--loop", action="store_true", help="반복 실행")
    ap.add_argument("--interval", type=int, default=300, help="반복 간격(초)")
    ap.add_argument("--budget", type=int, default=1_000_000, help="회당 최대 매수금액 (상한 1,000,000원)")
    ap.add_argument("--max-portfolio", type=int, default=20_000_000, help="포트폴리오 전체 매수가 상한")
    ap.add_argument("--max-daily-buys", type=int, default=3, help="하루 최대 매수 횟수")
    ap.add_argument("--universe", type=int, default=100)
    ap.add_argument("--short", type=int, default=20)
    ap.add_argument("--long", type=int, default=60)
    ap.add_argument("--popup", action="store_true", default=True, help="팝업 확인 사용")
    ap.add_argument("--no-popup", dest="popup", action="store_false", help="팝업 없이 제안만 기록")
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