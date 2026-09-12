from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from zoneinfo import ZoneInfo

from Function.KoreanStockQuotes import (
    KoreanStockQuoteClient,
    analyze_technical_indicators,
)


PROJECT_ROOT = Path(__file__).resolve().parent
HOLDINGS_PATH = PROJECT_ROOT / "Input" / "holdings.json"


def load_holdings() -> list[dict[str, object]]:
    if not HOLDINGS_PATH.exists():
        return []
    try:
        holdings = json.loads(HOLDINGS_PATH.read_text(encoding="utf-8"))
        return holdings if isinstance(holdings, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_holdings(holdings: list[dict[str, object]]) -> None:
    HOLDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLDINGS_PATH.write_text(
        json.dumps(holdings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_korean_market_open() -> bool:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) < (15, 30)


def load_local_env() -> None:
    # Streamlit Cloud secrets -> env (KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCESS_TOKEN)
    try:
        if hasattr(st, "secrets"):
            for key in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCESS_TOKEN"):
                try:
                    if key in st.secrets:
                        os.environ.setdefault(key, str(st.secrets[key]))
                except Exception:
                    pass
    except Exception:
        pass
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip())


load_local_env()

st.set_page_config(
    page_title="K-Equity Signal Desk",
    page_icon="K",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root { --ink: #17221f; --muted: #68736e; --mint: #d8f3e8; --coral: #ff725e; }
    .stApp { background: #f4f6f1; color: var(--ink); }
    [data-testid="stHeader"] { background: transparent; }
    .hero { padding: 1.6rem 0 1rem; border-bottom: 1px solid #d9e0d8; }
    .hero-kicker { color: #3d8067; font-size: .78rem; letter-spacing: .14em; font-weight: 700; }
    .hero h1 { margin: .25rem 0; font-size: 2.4rem; letter-spacing: -.03em; }
    .hero p { color: var(--muted); margin: 0; }
    .metric { background: white; border: 1px solid #dfe6df; padding: 1rem; border-radius: 8px; }
    .metric-label { color: var(--muted); font-size: .78rem; }
    .metric-value { font-size: 1.55rem; font-weight: 700; margin-top: .25rem; }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"] { color: var(--ink) !important; }
    </style>
    <div class="hero">
      <div class="hero-kicker">K-EQUITY / SIGNAL DESK</div>
      <h1>이동평균 돌파 감시</h1>
      <p>단기선과 장기선의 거리가 좁혀지는 국내 종목을 찾아 차트와 호가로 확인합니다.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=900, show_spinner=False)
def load_listing() -> pd.DataFrame:
    listing = fdr.StockListing("KRX")
    return listing[["Code", "Name", "Market", "Marcap"]].dropna(subset=["Code", "Name"])


@st.cache_data(ttl=900, show_spinner=False)
def load_history(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    history = fdr.DataReader(code, start_date, end_date).reset_index()
    return history.rename(
        columns={
            "Date": "일자",
            "Open": "시가",
            "High": "고가",
            "Low": "저가",
            "Close": "종가",
            "Volume": "거래량",
        }
    )


def score_candidate(row: pd.Series, short_window: int, long_window: int) -> dict[str, object] | None:
    try:
        history = load_history(row["Code"], scan_start, scan_end)
        if len(history) < long_window + 5:
            return None
        analysis = analyze_technical_indicators(history, short_window, long_window)
        latest = analysis.dropna(subset=["단기이동평균", "장기이동평균"]).iloc[-1]
        recent = analysis.dropna(subset=["단기이동평균"]).tail(5)
        short_slope = (recent["단기이동평균"].iloc[-1] - recent["단기이동평균"].iloc[0]) / latest["종가"]
        gap = (latest["단기이동평균"] - latest["장기이동평균"]) / latest["장기이동평균"]
        approaching = "골든크로스 임박" if gap < 0 else "데드크로스 임박"
        directional_slope = short_slope if gap < 0 else -short_slope
        score = max(0.0, 100 - abs(gap) * 1400) + max(0.0, min(30.0, directional_slope * 1500))
        if not latest["강한신호"]:
            return None
        strong_signal = latest["강한신호"]
        score += 25
        reasons = []
        if latest["단기이동평균"] > latest["장기이동평균"]:
            reasons.append("이동평균 상승추세")
        else:
            reasons.append("이동평균 하락추세")
        if latest["RSI"] < 35:
            reasons.append("RSI 과매도")
        elif latest["RSI"] > 65:
            reasons.append("RSI 과매수")
        if latest["MACD히스토그램"] > 0:
            reasons.append("MACD 상승")
        else:
            reasons.append("MACD 하락")
        if latest["종가"] <= latest["볼린저하단"]:
            reasons.append("볼린저 하단")
        elif latest["종가"] >= latest["볼린저상단"]:
            reasons.append("볼린저 상단")
        if latest["거래량배수"] >= 1.5:
            reasons.append("거래량 증가")
        return {
            "종목코드": row["Code"],
            "종목명": row["Name"],
            "시장": row["Market"],
            "현재가": latest["종가"],
            "단기선": latest["단기이동평균"],
            "장기선": latest["장기이동평균"],
            "이격률(%)": gap * 100,
            "단기선기울기(%)": short_slope * 100,
            "RSI": latest["RSI"],
            "MACD히스토그램": latest["MACD히스토그램"],
            "매수신호수": latest["매수신호수"],
            "매도신호수": latest["매도신호수"],
            "예상신호": strong_signal,
            "신호근거": ", ".join(reasons),
            "점수": score,
        }
    except Exception:
        return None


def evaluate_holding(
    holding: dict[str, object],
    short_window: int,
    long_window: int,
) -> dict[str, object] | None:
    try:
        history = load_history(holding["종목코드"], holding_start, scan_end)
        if len(history) < long_window:
            return None
        analysis = analyze_technical_indicators(history, short_window, long_window)
        latest = analysis.dropna(subset=["단기이동평균", "장기이동평균"]).iloc[-1]
        average_price = float(holding["평균매수가"])
        profit_rate = (latest["종가"] / average_price - 1) * 100
        sell_reasons = []
        if latest["단기이동평균"] < latest["장기이동평균"]:
            sell_reasons.append("이동평균 하락추세")
        if latest["RSI"] > 65:
            sell_reasons.append("RSI 과매수")
        if latest["MACD히스토그램"] < 0:
            sell_reasons.append("MACD 하락")
        if latest["종가"] >= latest["볼린저상단"]:
            sell_reasons.append("볼린저 상단")
        if latest["거래량배수"] >= 1.5:
            sell_reasons.append("거래량 증가")
        if profit_rate <= -8:
            sell_reasons.append("손실 제한")
        sell_score = len(sell_reasons)
        decision = "매도 검토" if sell_score >= 3 else "보유/관찰"
        return {
            "종목코드": holding["종목코드"],
            "종목명": holding["종목명"],
            "보유수량": holding["보유수량"],
            "평균매수가": average_price,
            "현재가": latest["종가"],
            "손익률(%)": profit_rate,
            "RSI": latest["RSI"],
            "매도조건수": sell_score,
            "판단": decision,
            "판단근거": ", ".join(sell_reasons) or "뚜렷한 매도 조건 없음",
        }
    except Exception:
        return None


with st.sidebar:
    st.markdown("### 스캔 설정")
    market = st.selectbox("시장", ["전체", "KOSPI", "KOSDAQ", "KONEX"])
    short_window = st.number_input("단기 이동평균", min_value=5, max_value=60, value=20, step=5)
    long_window = st.number_input("장기 이동평균", min_value=30, max_value=240, value=60, step=10)
    scan_limit = st.slider("스캔 종목 수", min_value=20, max_value=500, value=100, step=20)
    scan_button = st.button("강한 신호 다시 스캔", type="primary", use_container_width=True)
    st.caption("전 종목 호가를 동시에 호출하지 않고, 공개 일별 데이터로 후보를 선별합니다. 선택 종목의 현재 호가는 KIS API로 조회합니다.")
    st.markdown("### 보유 종목 등록")
    holding_code = st.text_input("종목코드", max_chars=6, placeholder="예: 005930")
    holding_quantity = st.number_input("보유수량", min_value=1, value=1, step=1)
    holding_average_price = st.number_input("평균매수가", min_value=0.01, value=10000.0, step=100.0)
    add_holding = st.button("보유 종목 추가", use_container_width=True)
    if "holdings" not in st.session_state:
        st.session_state["holdings"] = load_holdings()
    if add_holding:
        normalized_code = holding_code.strip().zfill(6)
        match = load_listing().loc[load_listing()["Code"] == normalized_code]
        if len(normalized_code) != 6 or not normalized_code.isdigit() or match.empty:
            st.error("유효한 국내 종목코드 6자리를 입력하세요.")
        else:
            st.session_state["holdings"] = [
                holding
                for holding in st.session_state["holdings"]
                if holding["종목코드"] != normalized_code
            ]
            st.session_state["holdings"].append(
                {
                    "종목코드": normalized_code,
                    "종목명": match.iloc[0]["Name"],
                    "보유수량": int(holding_quantity),
                    "평균매수가": float(holding_average_price),
                }
            )
            save_holdings(st.session_state["holdings"])
            st.success(f"{match.iloc[0]['Name']} 등록 완료")
    if st.session_state.get("holdings"):
        st.caption("같은 종목을 다시 등록하면 기존 정보가 갱신됩니다.")
        holding_options = {
            f"{holding['종목코드']} {holding['종목명']}": holding["종목코드"]
            for holding in st.session_state["holdings"]
        }
        selected_holding_label = st.selectbox("삭제할 보유 종목", holding_options)
        if st.button("선택 종목 삭제", use_container_width=True):
            selected_holding_code = holding_options[selected_holding_label]
            st.session_state["holdings"] = [
                holding
                for holding in st.session_state["holdings"]
                if holding["종목코드"] != selected_holding_code
            ]
            save_holdings(st.session_state["holdings"])
            st.rerun()
    st.markdown(
        """
        #### 신호 의미
        - **강한 매수**: 매수 조건 3개 이상 일치. 상승 추세, RSI 과매도, MACD 상승, 볼린저 하단 접근, 거래량 증가를 종합합니다.
        - **강한 매도**: 매도 조건 3개 이상 일치. 하락 추세, RSI 과매수, MACD 하락, 볼린저 상단 접근, 거래량 증가를 종합합니다.
        - **RSI**: 35 미만은 과매도, 65 초과는 과매수 구간으로 봅니다.
        - **MACD**: 단기 모멘텀이 장기 모멘텀보다 강한지 확인합니다.
        - **볼린저 밴드**: 가격이 하단에 가까우면 반등 가능성, 상단에 가까우면 조정 가능성을 살핍니다.
        - **거래량 증가**: 최근 20일 평균보다 거래량이 1.5배 이상인지 확인합니다.
        """
    )

if short_window >= long_window:
    st.error("단기 이동평균 기간은 장기 이동평균 기간보다 작아야 합니다.")
    st.stop()

listing = load_listing()
if market != "전체":
    listing = listing[listing["Market"].str.upper() == market]
listing = listing.sort_values("Marcap", ascending=False).head(scan_limit)

scan_end = datetime.now().strftime("%Y%m%d")
scan_start = (datetime.now() - timedelta(days=long_window * 3)).strftime("%Y%m%d")

scan_signature = (market, int(short_window), int(long_window), scan_limit)
if scan_button or st.session_state.get("scan_signature") != scan_signature:
    results: list[dict[str, object]] = []
    progress = st.progress(0, text="이동평균 후보를 스캔하는 중...")
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(score_candidate, row, short_window, long_window) for _, row in listing.iterrows()]
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result:
                results.append(result)
            progress.progress(index / len(futures), text=f"후보 스캔 {index}/{len(futures)}")
    progress.empty()
    st.session_state["candidate_results"] = pd.DataFrame(results)
    st.session_state["scan_signature"] = scan_signature

candidates = st.session_state["candidate_results"]
holdings = st.session_state.get("holdings", [])
holding_start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
if holdings:
    holding_results = [
        result
        for holding in holdings
        if (result := evaluate_holding(holding, short_window, long_window)) is not None
    ]
    if holding_results:
        st.subheader("보유 종목 매도 판단")
        st.caption("매도 판단은 기술지표와 손익률을 이용한 참고 신호이며, 투자 결정을 자동으로 대신하지 않습니다.")
        st.dataframe(
            pd.DataFrame(holding_results).style.format(
                {"평균매수가": "{:,.0f}", "현재가": "{:,.0f}", "손익률(%)": "{:.2f}", "RSI": "{:.1f}"}
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("등록한 보유 종목의 분석 데이터를 충분히 가져오지 못했습니다.")
if candidates.empty:
    st.warning("조건을 만족하는 후보가 없습니다. 스캔 범위를 늘려보세요.")
    st.stop()

candidates = candidates.sort_values("점수", ascending=False).reset_index(drop=True)
first = candidates.iloc[0]

metric_columns = st.columns(4)
metric_columns[0].markdown(f'<div class="metric"><div class="metric-label">스캔 대상</div><div class="metric-value">{len(listing):,}개</div></div>', unsafe_allow_html=True)
metric_columns[1].markdown(f'<div class="metric"><div class="metric-label">강한 매수</div><div class="metric-value">{(candidates["예상신호"] == "강한 매수").sum():,}개</div></div>', unsafe_allow_html=True)
metric_columns[2].markdown(f'<div class="metric"><div class="metric-label">강한 매도</div><div class="metric-value">{(candidates["예상신호"] == "강한 매도").sum():,}개</div></div>', unsafe_allow_html=True)
metric_columns[3].markdown(f'<div class="metric"><div class="metric-label">최상위 후보</div><div class="metric-value">{first["종목명"]}</div></div>', unsafe_allow_html=True)

st.subheader("돌파 후보")
selected_code = st.selectbox(
    "상세 분석 종목",
    candidates["종목코드"].tolist(),
    format_func=lambda code: f"{code}  {candidates.loc[candidates['종목코드'] == code, '종목명'].iloc[0]}",
)

st.dataframe(
    candidates.style.format(
        {"현재가": "{:,.0f}", "단기선": "{:,.0f}", "장기선": "{:,.0f}", "이격률(%)": "{:.2f}", "단기선기울기(%)": "{:.2f}", "RSI": "{:.1f}", "MACD히스토그램": "{:.2f}", "점수": "{:.1f}"}
    ),
    use_container_width=True,
    hide_index=True,
)

selected = candidates[candidates["종목코드"] == selected_code].iloc[0]
history_start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
history = load_history(selected_code, history_start, scan_end)
analysis = analyze_technical_indicators(history, short_window, long_window)

with st.container():
    st.subheader("현재 신호")
    signal_metrics = st.columns(5)
    signal_metrics[0].metric("판정", selected["예상신호"])
    signal_metrics[1].metric("이격률", f"{selected['이격률(%)']:.2f}%")
    signal_metrics[2].metric("후보 점수", f"{selected['점수']:.1f}")
    signal_metrics[3].metric("RSI", f"{selected['RSI']:.1f}")
    signal_metrics[4].metric("신호 일치", f"매수 {int(selected['매수신호수'])} / 매도 {int(selected['매도신호수'])}")
    st.caption("강한 신호는 이동평균 추세, RSI, MACD, 볼린저 밴드, 거래량 중 3개 이상이 같은 방향일 때만 표시합니다.")

st.subheader(f"{selected['종목명']} · 가격과 이동평균")
price_chart = go.Figure()
price_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["종가"], name="종가", line={"color": "#17221f", "width": 1.5}))
price_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["단기이동평균"], name=f"단기선 {short_window}일", line={"color": "#2584a8", "width": 2}))
price_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["장기이동평균"], name=f"장기선 {long_window}일", line={"color": "#ef8a4c", "width": 2}))
price_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["볼린저상단"], name="볼린저 상단", line={"color": "#a6b5ae", "width": 1, "dash": "dot"}))
price_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["볼린저하단"], name="볼린저 하단", line={"color": "#a6b5ae", "width": 1, "dash": "dot"}))
price_chart.update_layout(height=620, margin={"l": 10, "r": 10, "t": 30, "b": 10}, hovermode="x unified", template="plotly_white", legend={"orientation": "h", "y": 1.02})
st.plotly_chart(price_chart, use_container_width=True)

st.subheader("RSI(14)")
rsi_chart = go.Figure(go.Scatter(x=analysis["일자"], y=analysis["RSI"], name="RSI(14)", line={"color": "#805ad5", "width": 2}))
rsi_chart.add_hline(y=70, line_dash="dot", line_color="#d26a5a", annotation_text="과매수 70")
rsi_chart.add_hline(y=30, line_dash="dot", line_color="#3d8067", annotation_text="과매도 30")
rsi_chart.update_yaxes(range=[0, 100], title_text="RSI")
rsi_chart.update_layout(height=330, margin={"l": 10, "r": 10, "t": 30, "b": 10}, hovermode="x unified", template="plotly_white", showlegend=False)
st.plotly_chart(rsi_chart, use_container_width=True)

st.subheader("MACD")
macd_chart = go.Figure()
macd_chart.add_trace(go.Bar(x=analysis["일자"], y=analysis["MACD히스토그램"], name="히스토그램", marker_color="#b9c9c2", opacity=0.65))
macd_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["MACD"], name="MACD", line={"color": "#2584a8", "width": 2}))
macd_chart.add_trace(go.Scatter(x=analysis["일자"], y=analysis["MACD신호선"], name="신호선", line={"color": "#ef8a4c", "width": 2}))
macd_chart.update_layout(height=380, margin={"l": 10, "r": 10, "t": 30, "b": 10}, hovermode="x unified", template="plotly_white", legend={"orientation": "h", "y": 1.02})
st.plotly_chart(macd_chart, use_container_width=True)

@st.fragment(run_every="5s" if is_korean_market_open() else None)
def render_live_quote() -> None:
    st.subheader("현재 호가")
    market_open = is_korean_market_open()
    if market_open:
        current_order_book = pd.DataFrame()
        try:
            quote_client = KoreanStockQuoteClient()
            current_order_book = quote_client.get_current_order_book(selected_code)
            current_price = quote_client.last_current_price
            if current_price is not None:
                st.metric("실시간 현재가", f"{current_price:,.0f}원")
            st.caption("장중: 5초마다 KIS 현재 호가를 갱신합니다.")
            st.dataframe(current_order_book, use_container_width=True, hide_index=True)
        except Exception as error:
            st.warning(f"실시간 호가를 가져오지 못했습니다. 마지막 거래일 데이터를 표시합니다: {error}")
            st.dataframe(current_order_book, use_container_width=True, hide_index=True)
    else:
        latest_close = history.dropna(subset=["종가"]).iloc[-1]["종가"]
        st.metric("최근 거래일 종가", f"{latest_close:,.0f}원")
        st.caption("현재 휴장 시간입니다. 마지막 거래일 종가 기준으로 표시합니다.")
        st.dataframe(
            pd.DataFrame(
                {
                    "구분": ["최근 거래일 종가"],
                    "가격": [latest_close],
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


render_live_quote()
