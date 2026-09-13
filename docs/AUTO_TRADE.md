# 자동매매 시스템 (REST 기반)

첨부 `expert_manual.pdf`는 eFriend Expert OCX/COM(Windows 전용, 관리자권한+MFC+eFriend 실행 필요) 방식입니다.
Streamlit Cloud(Linux)와 GitHub 공개 배포에서는 동작하지 않으므로, 본 프로젝트는 REST Open API로 자동매매를 구현합니다.

## 구조

- `Function/KoreanStockQuotes.py`: 시세 + 강한 매수/매도 신호 (`매수신호수>=3` → 강한 매수)
- `Function/KoreanStockTrading.py`: `KISOrderClient` (현금 매수 TTTC0802U / 매도 TTTC0801U, 잔고 TTTC8434R, 체결 TTTC8001R)
- `RunTool/auto_trade.py`: 신호 → 주문 러너 (기본 dry-run, 모의 기본)

## 매수/매도 규칙

- 매수: 당일 강한 매수 중 점수(`100-|이격|*1400 + 기울기*1500 + 25`) 1등 1종목, 예산 내 수량, 종가 기준 시장가(또는 지정가)
- 매도: 보유잔고 중 `매도신호수>=2` 전량 시장가
- 매도신호 5개: 추세하락, RSI>65, MACD하향교차, 종가>=볼린저상단, 거래량>=1.5배

## 환경변수

```
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT=12345678-01
KIS_IS_PAPER=1   # 1=모의(https://openapivts...:29443), 0=실전
```

## 실행

```bash
# 안전 테스트 (주문 전송 없음)
python RunTool/auto_trade.py --once

# 모의 실주문 1회
python RunTool/auto_trade.py --live --once --budget 1000000 --universe 100

# 5분 간격 반복 (모의 실주문)
python RunTool/auto_trade.py --live --loop --interval 300

# 실전 (주의! 소액+모의 먼저 검증)
python RunTool/auto_trade.py --real --live --once --budget 100000
```

로그: `Input/auto_trade_log.csv` (git 제외)

## 주의

- 실주문은 100ms TR 제한 준수를 위해 주문 간 0.15초 대기합니다.
- Streamlit Cloud에서는 장시간 루프 실행이 중단될 수 있어, 실운용은 로컬 PC/서버에서 `--loop` 실행을 권장합니다.
- 실전 전 반드시 모의투자에서 `dry-run → live(모의)` 순서로 검증하세요.