# -*- coding: utf-8 -*-
from pathlib import Path
import json
p = Path(__file__).resolve().parent.parent / "NoteBook" / "NoteBook.ipynb"
nb = json.loads(p.read_text(encoding="utf-8"))
for c in nb["cells"]:
    src = "".join(c.get("source", []))
    if "auto-trade-buy" in c.get("id", "") or ("매수 제안: 정규장" in src and "BUY_BUDGET" in src):
        c["source"] = [
            "# ===== 매수 제안: 정규장만, 회당 100만원, 포트폴리오 2000만원, 일 3회, 현시간가 =====\n",
            "import pandas as pd\n",
            "from pathlib import Path as _P\n",
            "PER_BUY_CAP = min(BUY_BUDGET, 1_000_000)\n",
            "MAX_PORTFOLIO = 20_000_000\n",
            "MAX_DAILY_BUYS = 3\n",
            "logp = project_root / 'Input' / 'auto_trade_log.csv'\n",
            "today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')\n",
            "today_buys = 0\n",
            "if logp.exists():\n",
            "    try:\n",
            "        _df = pd.read_csv(logp, encoding='utf-8-sig')\n",
            "        today_buys = int(((_df['side']=='BUY') & _df['time'].astype(str).str.startswith(today)).sum())\n",
            "    except Exception:\n",
            "        today_buys = 0\n",
            "try:\n",
            "    _bal = order_client.get_balance()\n",
            "    _cost = sum(float(str(r.get('hldg_qty',0) or 0))*float(str(r.get('pchs_avg_pric', r.get('avg_prvs',0)) or 0)) for _, r in _bal.iterrows())\n",
            "except Exception:\n",
            "    _cost = 0\n",
            "print(f'금일매수 {today_buys}/{MAX_DAILY_BUYS}, 포트폴리오 { _cost:,.0f}/{MAX_PORTFOLIO:,}')\n",
            "if not is_market_open():\n",
            "    print('정규시장 시간 아님(평일 09:00~15:30) → 매수 스킵')\n",
            "elif today_buys >= MAX_DAILY_BUYS:\n",
            "    print(f'일일 최대 {MAX_DAILY_BUYS}회 초과 → 매수 스킵')\n",
            "elif _cost >= MAX_PORTFOLIO:\n",
            "    print(f'포트폴리오 {MAX_PORTFOLIO:,}원 초과 → 매수 스킵')\n",
            "else:\n",
            "    uni = fdr.StockListing('KRX')[['Code','Name','Market','Marcap']].dropna().sort_values('Marcap', ascending=False).head(100)\n",
            "    rows = []\n",
            "    for _, r in uni.iterrows():\n",
            "        try:\n",
            "            h = fdr.DataReader(r['Code'], start_date, end_date).reset_index().rename(columns={'Date':'일자','Open':'시가','High':'고가','Low':'저가','Close':'종가','Volume':'거래량'})\n",
            "            a = analyze_technical_indicators(h, 20, 60)\n",
            "            last = a.dropna(subset=['단기이동평균','장기이동평균']).iloc[-1]\n",
            "            if last['강한신호'] == '강한 매수':\n",
            "                gap = (last['단기이동평균']-last['장기이동평균'])/last['장기이동평균']\n",
            "                score = max(0, 100-abs(gap)*1400) + 25\n",
            "                rows.append((score, r['Code'], r['Name']))\n",
            "        except Exception:\n",
            "            pass\n",
            "    rows = sorted(rows, reverse=True)[:max(0, MAX_DAILY_BUYS-today_buys)]\n",
            "    print(f'강한 매수 후보: {len(rows)}개')\n",
            "    display(pd.DataFrame(rows, columns=['점수','코드','종목명']).head())\n",
            "    for _, code, name in rows:\n",
            "        px = live_price(code)\n",
            "        print(f'{code} {name} 현시간가={px}')\n",
            "        if px is None:\n",
            "            print('현시간가 실패 → 스킵'); continue\n",
            "        left = min(PER_BUY_CAP, MAX_PORTFOLIO-_cost)\n",
            "        qty = int(left // px) if px>0 else 0\n",
            "        if qty<=0:\n",
            "            print('예산 부족 → 스킵'); continue\n",
            "        if popup_ask('매수 확인', f'[{code} {name}] {px:,.0f}원 x {qty}주 지정가 매수할까요? (금일 {today_buys+1}/{MAX_DAILY_BUYS})'):\n",
            "            print(order_client.order_cash(code, qty, int(px), side='buy', ord_dvsn='01', dry_run=not LIVE_ORDER))\n",
            "            today_buys+=1; _cost+=qty*px\n",
            "            if today_buys>=MAX_DAILY_BUYS: break\n",
            "        else:\n",
            "            print('매수 스킵(팝업 거절)')\n",
        ]
    if "매도 제안" in src and "매도신호수" in src:
        # ensure >=3 (already) - keep
        pass
p.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print("patched notebook buy limits")