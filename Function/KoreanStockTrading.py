# -*- coding: utf-8 -*-
"""한국투자증권 REST Open API 기반 국내주식 자동주문 모듈.

첨부된 expert_manual.pdf(eFriend Expert OCX/COM)는 Windows 전용이며
관리자권한+MFC+eFriend 실행이 필요해 Streamlit Cloud(Linux)에서는 동작하지 않습니다.
따라서 자동매매는 현재 프로젝트와 동일한 REST Open API로 구현합니다.

- 실전: https://openapi.koreainvestment.com:9443
- 모의:  https://openapivts.koreainvestment.com:29443
- 현금 매수: POST /uapi/domestic-stock/v1/trading/order-cash  TR_ID=TTTC0802U
- 현금 매도: POST /uapi/domestic-stock/v1/trading/order-cash  TR_ID=TTTC0801U
- 잔고조회: GET  /uapi/domestic-stock/v1/trading/inquire-balance  TR_ID=TTTC8434R
- 주문체결조회: GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld  TR_ID=TTTC8001R
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests


REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_PATH = "/oauth2/tokenP"
ORDER_CASH_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
BALANCE_PATH = "/uapi/domestic-stock/v1/trading/inquire-balance"
DAILY_CCLD_PATH = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
HASHKEY_PATH = "/uapi/hashkey"

BUY_TR_ID = "TTTC0802U"
SELL_TR_ID = "TTTC0801U"
BALANCE_TR_ID = "TTTC8434R"
DAILY_CCLD_TR_ID = "TTTC8001R"


@dataclass
class KISAccount:
    cano: str  # 종합계좌번호 앞 8자리
    prdt_cd: str  # 계좌상품코드 뒤 2자리 (보통 01)
    is_paper: bool = True

    @classmethod
    def from_env(cls, is_paper: bool | None = None) -> "KISAccount":
        raw = os.getenv("KIS_ACCOUNT", "").strip().replace("-", "")
        # KIS_ACCOUNT 예: 12345678-01 또는 1234567801
        if len(raw) == 10 and raw.isdigit():
            cano, prdt = raw[:8], raw[8:]
        else:
            cano = os.getenv("KIS_CANO", "").strip()
            prdt = os.getenv("KIS_ACNT_PRDT_CD", "01").strip()
        if not cano:
            raise ValueError("KIS_ACCOUNT(예: 12345678-01) 또는 KIS_CANO 환경변수가 필요합니다.")
        paper = is_paper
        if paper is None:
            paper = os.getenv("KIS_IS_PAPER", "1").strip() not in ("0", "false", "False", "N")
        return cls(cano=cano, prdt_cd=prdt or "01", is_paper=paper)


class KISOrderClient:
    """현금 매수/매도 + 잔고/체결조회. 기본 dry_run=True로 안전하게 동작."""

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        account: KISAccount | None = None,
        is_paper: bool | None = None,
        timeout: int = 15,
    ) -> None:
        self.app_key = app_key or os.getenv("KIS_APP_KEY")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET")
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET 환경변수가 필요합니다.")
        self.account = account or KISAccount.from_env(is_paper=is_paper)
        self.base_url = (PAPER_BASE_URL if self.account.is_paper else REAL_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._access_token: str | None = os.getenv("KIS_ACCESS_TOKEN") or None

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        resp = self._session.post(
            f"{self.base_url}{TOKEN_PATH}",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise RuntimeError(f"토큰 발급 실패: {body}")
        self._access_token = token
        return token

    def _get_hashkey(self, body: dict[str, Any]) -> str:
        resp = self._session.post(
            f"{self.base_url}{HASHKEY_PATH}",
            headers={"content-type": "application/json", "appkey": self.app_key, "appsecret": self.app_secret},
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["HASH"]

    def _headers(self, tr_id: str, hashkey: str = "") -> dict[str, str]:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._get_access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def order_cash(
        self,
        code: str,
        qty: int,
        price: int = 0,
        side: str = "buy",
        ord_dvsn: str = "01",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """현금 주문. side=buy/sell. ord_dvsn=01 지정가, 02 시장가(가격 0).
        dry_run=True면 실제 전송 없이 주문 페이로드만 반환 (기본 안전장치)."""
        code = str(code).strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            raise ValueError("종목코드는 6자리 숫자여야 합니다.")
        if qty <= 0:
            raise ValueError("수량은 1 이상이어야 합니다.")
        tr_id = BUY_TR_ID if side == "buy" else SELL_TR_ID
        body = {
            "CANO": self.account.cano,
            "ACNT_PRDT_CD": self.account.prdt_cd,
            "PDNO": code,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        if dry_run:
            return {
                "dry_run": True,
                "is_paper": self.account.is_paper,
                "tr_id": tr_id,
                "body": body,
                "message": "dry_run이므로 실제 주문을 전송하지 않았습니다.",
            }
        # 실전/모의 실주문은 100ms TR 제한 준수를 위해 호출 간격을 둔다.
        time.sleep(0.15)
        hashkey = self._get_hashkey(body)
        resp = self._session.post(
            f"{self.base_url}{ORDER_CASH_PATH}",
            headers=self._headers(tr_id, hashkey),
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        out = resp.json()
        if out.get("rt_cd") != "0":
            raise RuntimeError(f"주문 거부 ({out.get('msg_cd')}): {out.get('msg1')}")
        return out

    def get_balance(self) -> pd.DataFrame:
        params = {
            "CANO": self.account.cano,
            "ACNT_PRDT_CD": self.account.prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        time.sleep(0.15)
        resp = self._session.get(
            f"{self.base_url}{BALANCE_PATH}",
            headers=self._headers(BALANCE_TR_ID),
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"잔고조회 오류 ({body.get('msg_cd')}): {body.get('msg1')}")
        return pd.DataFrame(body.get("output1", []))

    def get_daily_ccld(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.account.cano,
            "ACNT_PRDT_CD": self.account.prdt_cd,
            "INQR_STRT_DT": start or today,
            "INQR_END_DT": end or today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "00",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        time.sleep(0.15)
        resp = self._session.get(
            f"{self.base_url}{DAILY_CCLD_PATH}",
            headers=self._headers(DAILY_CCLD_TR_ID),
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("rt_cd") != "0":
            raise RuntimeError(f"체결조회 오류 ({body.get('msg_cd')}): {body.get('msg1')}")
        return pd.DataFrame(body.get("output1", []))


def append_trade_log(log_path: str | Path, record: dict[str, Any]) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": datetime.now().isoformat(timespec="seconds"), **record}
    df = pd.DataFrame([row])
    if path.exists():
        df.to_csv(path, mode="a", header=False, index=False, encoding="utf-8-sig")
    else:
        df.to_csv(path, index=False, encoding="utf-8-sig")