# -*- coding: utf-8 -*-

"""한국투자증권 Open API를 이용한 국내주식 시세 조회."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Iterator

import pandas as pd
import requests


_BASE_URL = "https://openapi.koreainvestment.com:9443"
_TOKEN_PATH = "/oauth2/tokenP"
_APPROVAL_PATH = "/oauth2/Approval"
_WEBSOCKET_URL = "ws://ops.koreainvestment.com:21000/tryitout"
_ORDER_BOOK_PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
_DAILY_PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
_REALTIME_ORDER_BOOK_ID = "H0STASP0"
_ACCESS_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class KoreanStockQuoteClient:
    """한국투자증권 국내주식 현재 호가와 과거 일별 시세 클라이언트."""

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        access_token: str | None = None,
        base_url: str = _BASE_URL,
        timeout: int = 15,
    ) -> None:
        self.app_key = app_key or os.getenv("KIS_APP_KEY")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET")
        self.access_token = access_token or os.getenv("KIS_ACCESS_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self.last_current_price: float | int | None = None

        if not self.app_key or not self.app_secret:
            raise ValueError(
                "KIS_APP_KEY와 KIS_APP_SECRET 환경변수 또는 생성자 인자가 필요합니다."
            )

    def _get_access_token(self) -> str:
        if self.access_token:
            return self.access_token
        cached_token = _ACCESS_TOKEN_CACHE.get(self.app_key)
        if cached_token and cached_token[1] > time.time() + 60:
            self.access_token = cached_token[0]
            return self.access_token

        response = self._session.post(
            f"{self.base_url}{_TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=self.timeout,
        )
        if response.status_code == 403:
            raise RuntimeError(
                "KIS 접근 토큰 발급이 거부되었습니다. "
                "토큰 발급 제한(분당 1회 등) 또는 앱 권한/IP 설정을 확인하세요."
            )
        response.raise_for_status()
        body = response.json()
        self.access_token = body.get("access_token")
        if not self.access_token:
            raise RuntimeError(f"접근 토큰 응답에 access_token이 없습니다: {body}")
        expires_in = int(body.get("expires_in", 86400))
        _ACCESS_TOKEN_CACHE[self.app_key] = (
            self.access_token,
            time.time() + expires_in,
        )
        return self.access_token

    def _request(
        self,
        path: str,
        transaction_id: str,
        params: dict[str, str],
    ) -> dict[str, Any]:
        response = self._session.get(
            f"{self.base_url}{path}",
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._get_access_token()}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": transaction_id,
                "custtype": "P",
            },
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(
                f"KIS API 오류 ({body.get('msg_cd')}): {body.get('msg1')}"
            )
        return body

    @staticmethod
    def _validate_code(stock_code: str) -> str:
        stock_code = str(stock_code).strip()
        if len(stock_code) != 6 or not stock_code.isdigit():
            raise ValueError("stock_code는 6자리 숫자 종목코드여야 합니다. 예: '005930'")
        return stock_code

    @staticmethod
    def _validate_date(value: str, name: str) -> str:
        try:
            parsed = datetime.strptime(str(value), "%Y%m%d")
        except ValueError as error:
            raise ValueError(f"{name}은 YYYYMMDD 형식이어야 합니다.") from error
        return parsed.strftime("%Y%m%d")

    def get_current_order_book(self, stock_code: str) -> pd.DataFrame:
        """현재 매도/매수 10단 호가와 잔량을 행 단위 DataFrame으로 반환."""
        code = self._validate_code(stock_code)
        body = self._request(
            _ORDER_BOOK_PATH,
            "FHKST01010200",
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
        output = body.get("output1", body.get("output", {}))
        self.last_current_price = _to_number(output.get("stck_prpr"))
        rows = []
        for level in range(1, 11):
            rows.append(
                {
                    "호가단계": level,
                    "매도호가": _to_number(output.get(f"askp{level}")),
                    "매도잔량": _to_number(output.get(f"askp_rsqn{level}")),
                    "매수호가": _to_number(output.get(f"bidp{level}")),
                    "매수잔량": _to_number(output.get(f"bidp_rsqn{level}")),
                }
            )
        return pd.DataFrame(rows)

    def _get_approval_key(self) -> str:
        response = self._session.post(
            f"{self.base_url}{_APPROVAL_PATH}",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "secretkey": self.app_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        approval_key = response.json().get("approval_key")
        if not approval_key:
            raise RuntimeError("실시간 접속 승인 응답에 approval_key가 없습니다.")
        return approval_key

    def stream_order_book(
        self,
        stock_code: str,
        max_updates: int | None = None,
        idle_timeout: int | None = 5,
    ) -> Iterator[pd.DataFrame]:
        """실시간 호가 변경을 DataFrame으로 계속 반환합니다."""
        import websocket

        code = self._validate_code(stock_code)
        websocket_client = websocket.create_connection(
            _WEBSOCKET_URL,
            timeout=self.timeout,
        )
        try:
            if idle_timeout is not None:
                websocket_client.settimeout(idle_timeout)
            websocket_client.send(
                json.dumps(
                    {
                        "header": {
                            "approval_key": self._get_approval_key(),
                            "custtype": "P",
                            "tr_type": "1",
                            "content-type": "utf-8",
                        },
                        "body": {
                            "input": {
                                "tr_id": _REALTIME_ORDER_BOOK_ID,
                                "tr_key": code,
                            }
                        },
                    }
                )
            )
            update_count = 0
            while max_updates is None or update_count < max_updates:
                try:
                    message = websocket_client.recv()
                except websocket.WebSocketTimeoutException:
                    return
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                parts = message.split("|", 3)
                if len(parts) < 4 or parts[1] != _REALTIME_ORDER_BOOK_ID:
                    continue
                if parts[0] != "0":
                    raise RuntimeError("실시간 호가 데이터가 암호화되어 있어 해독할 수 없습니다.")
                fields = parts[3].split("^")
                if len(fields) < 45:
                    continue
                rows = []
                for level in range(10):
                    rows.append(
                        {
                            "호가단계": level + 1,
                            "매도호가": _to_number(fields[5 + level]),
                            "매도잔량": _to_number(fields[15 + level]),
                            "매수호가": _to_number(fields[25 + level]),
                            "매수잔량": _to_number(fields[35 + level]),
                        }
                    )
                update_count += 1
                yield pd.DataFrame(rows)
        finally:
            websocket_client.close()

    def get_historical_prices(
        self,
        stock_code: str,
        start_date: str,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """기간 내 일별 OHLCV를 반환. 과거 시점의 호가창 스냅샷은 KIS API에서 제공하지 않음."""
        code = self._validate_code(stock_code)
        start = self._validate_date(start_date, "start_date")
        end = self._validate_date(end_date or datetime.now().strftime("%Y%m%d"), "end_date")
        if start > end:
            raise ValueError("start_date는 end_date보다 빠르거나 같아야 합니다.")

        rows = []
        current_end = end
        while current_end >= start:
            window_start = max(
                start,
                (
                    datetime.strptime(current_end, "%Y%m%d")
                    - timedelta(days=29)
                ).strftime("%Y%m%d"),
            )
            body = self._request(
                _DAILY_PRICE_PATH,
                "FHKST01010400",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": window_start,
                    "FID_INPUT_DATE_2": current_end,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "1",
                },
            )
            page = body.get("output1", body.get("output", []))
            rows.extend(page)
            if not page:
                break
            oldest_date = min(row["stck_bsop_date"] for row in page)
            if oldest_date <= start:
                break
            next_end = (
                datetime.strptime(window_start, "%Y%m%d") - timedelta(days=1)
            ).strftime("%Y%m%d")
            if next_end >= current_end:
                break
            current_end = next_end
        result = pd.DataFrame(
            [
                {
                    "일자": row.get("stck_bsop_date"),
                    "시가": _to_number(row.get("stck_oprc")),
                    "고가": _to_number(row.get("stck_hgpr")),
                    "저가": _to_number(row.get("stck_lwpr")),
                    "종가": _to_number(row.get("stck_clpr")),
                    "거래량": _to_number(row.get("acml_vol")),
                    "거래대금": _to_number(row.get("acml_tr_pbmn")),
                }
                for row in rows
            ]
        )
        if not result.empty:
            result["일자"] = pd.to_datetime(result["일자"], format="%Y%m%d")
            result = result.sort_values("일자").reset_index(drop=True)
        return result


def _to_number(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    number = float(str(value).replace(",", ""))
    return int(number) if number.is_integer() else number


def get_domestic_stock_list(market: str = "KRX") -> pd.DataFrame:
    """국내 거래소의 종목코드, 종목명, 시장을 반환합니다."""
    import FinanceDataReader as fdr

    normalized_market = market.upper()
    if normalized_market not in {"KRX", "KOSPI", "KOSDAQ", "KONEX"}:
        raise ValueError("market은 KRX, KOSPI, KOSDAQ, KONEX 중 하나여야 합니다.")

    listing = fdr.StockListing(normalized_market)
    return (
        listing[["Code", "Name", "Market"]]
        .rename(columns={"Code": "종목코드", "Name": "종목명", "Market": "시장"})
        .sort_values("종목코드")
        .reset_index(drop=True)
    )


def analyze_moving_averages(
    prices: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 60,
) -> pd.DataFrame:
    """종가 기준 단기·장기 이동평균과 교차 신호를 계산합니다."""
    if short_window <= 0 or long_window <= 0:
        raise ValueError("이동평균 기간은 1 이상이어야 합니다.")
    if short_window >= long_window:
        raise ValueError("short_window는 long_window보다 작아야 합니다.")
    if "종가" not in prices.columns:
        raise ValueError("prices에 '종가' 컬럼이 필요합니다.")

    result = prices.copy()
    if "일자" in result.columns:
        result = result.sort_values("일자").reset_index(drop=True)
    close = pd.to_numeric(result["종가"], errors="coerce")
    result["단기이동평균"] = close.rolling(short_window).mean()
    result["장기이동평균"] = close.rolling(long_window).mean()
    result["추세"] = "관찰"
    result.loc[result["단기이동평균"] > result["장기이동평균"], "추세"] = "상승"
    result.loc[result["단기이동평균"] < result["장기이동평균"], "추세"] = "하락"

    previous_short = result["단기이동평균"].shift(1)
    previous_long = result["장기이동평균"].shift(1)
    result["신호"] = ""
    golden_cross = (result["단기이동평균"] > result["장기이동평균"]) & (
        previous_short <= previous_long
    )
    dead_cross = (result["단기이동평균"] < result["장기이동평균"]) & (
        previous_short >= previous_long
    )
    result.loc[golden_cross, "신호"] = "골든크로스"
    result.loc[dead_cross, "신호"] = "데드크로스"
    return result


def analyze_technical_indicators(
    prices: pd.DataFrame,
    short_window: int = 20,
    long_window: int = 60,
    rsi_window: int = 14,
    bollinger_window: int = 20,
) -> pd.DataFrame:
    """이동평균, RSI, MACD, 볼린저 밴드, 거래량 신호를 함께 계산합니다."""
    if rsi_window <= 0 or bollinger_window <= 0:
        raise ValueError("RSI와 볼린저 밴드 기간은 1 이상이어야 합니다.")
    if "종가" not in prices.columns:
        raise ValueError("prices에 '종가' 컬럼이 필요합니다.")

    result = analyze_moving_averages(prices, short_window, long_window)
    close = pd.to_numeric(result["종가"], errors="coerce")
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(rsi_window).mean()
    losses = (-delta.clip(upper=0)).rolling(rsi_window).mean()
    relative_strength = gains / losses.replace(0, pd.NA)
    result["RSI"] = 100 - (100 / (1 + relative_strength))
    result.loc[(losses == 0) & (gains > 0), "RSI"] = 100

    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    result["MACD"] = ema_fast - ema_slow
    result["MACD신호선"] = result["MACD"].ewm(span=9, adjust=False).mean()
    result["MACD히스토그램"] = result["MACD"] - result["MACD신호선"]

    middle_band = close.rolling(bollinger_window).mean()
    band_width = close.rolling(bollinger_window).std() * 2
    result["볼린저중간"] = middle_band
    result["볼린저상단"] = middle_band + band_width
    result["볼린저하단"] = middle_band - band_width

    if "거래량" in result.columns:
        volume = pd.to_numeric(result["거래량"], errors="coerce")
        result["거래량배수"] = volume / volume.rolling(20).mean()
    else:
        result["거래량배수"] = pd.NA

    previous_macd = result["MACD"].shift(1)
    previous_signal = result["MACD신호선"].shift(1)
    macd_golden_cross = (result["MACD"] > result["MACD신호선"]) & (
        previous_macd <= previous_signal
    )
    macd_dead_cross = (result["MACD"] < result["MACD신호선"]) & (
        previous_macd >= previous_signal
    )
    buy_conditions = pd.DataFrame(
        {
            "추세상승": result["단기이동평균"] > result["장기이동평균"],
            "RSI과매도": result["RSI"] < 35,
            "MACD상향교차": macd_golden_cross,
            "하단밴드접근": close <= result["볼린저하단"],
            "거래량확인": result["거래량배수"] >= 1.5,
        }
    )
    sell_conditions = pd.DataFrame(
        {
            "추세하락": result["단기이동평균"] < result["장기이동평균"],
            "RSI과매수": result["RSI"] > 65,
            "MACD하향교차": macd_dead_cross,
            "상단밴드접근": close >= result["볼린저상단"],
            "거래량확인": result["거래량배수"] >= 1.5,
        }
    )
    result["매수신호수"] = buy_conditions.fillna(False).sum(axis=1)
    result["매도신호수"] = sell_conditions.fillna(False).sum(axis=1)
    result["강한신호"] = ""
    result.loc[result["매수신호수"] >= 3, "강한신호"] = "강한 매수"
    result.loc[result["매도신호수"] >= 3, "강한신호"] = "강한 매도"
    return result


def get_current_order_book(
    stock_code: str,
    app_key: str | None = None,
    app_secret: str | None = None,
) -> pd.DataFrame:
    """현재 국내주식 10단 호가를 조회하는 편의 함수."""
    return KoreanStockQuoteClient(app_key, app_secret).get_current_order_book(stock_code)


def get_historical_prices(
    stock_code: str,
    start_date: str,
    end_date: str | None = None,
    app_key: str | None = None,
    app_secret: str | None = None,
) -> pd.DataFrame:
    """국내주식 기간별 일별 가격을 조회하는 편의 함수."""
    return KoreanStockQuoteClient(app_key, app_secret).get_historical_prices(
        stock_code, start_date, end_date
    )
