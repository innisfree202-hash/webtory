# ============================================================
# CallDate.py
#
# Bermudan Callable Swap
# Callable Zero Coupon Bond
# Callable Swaption
#
# Call Date Generator
#
# 기능:
#   1. trade_date ~ maturity 사이의 정기 Call Date 생성
#   2. frequency = 3M / 6M / 1Y 지원
#   3. valuation_date 이후의 날짜만 생성
#   4. trade_date는 Call Date에서 제외
#   5. maturity는 Call Date에서 제외
# ============================================================

import QuantLib as ql


# ============================================================
# 1. Frequency 변환
# ============================================================

def _frequency_to_period(frequency):

    if not isinstance(frequency, str):
        raise TypeError(
            "frequency는 문자열이어야 합니다. "
            "예: '3M', '6M', '1Y'"
        )

    frequency = frequency.upper().strip()

    if frequency == "3M":

        return ql.Period(
            3,
            ql.Months
        )

    elif frequency == "6M":

        return ql.Period(
            6,
            ql.Months
        )

    elif frequency == "1Y":

        return ql.Period(
            1,
            ql.Years
        )

    else:

        raise ValueError(
            "지원하지 않는 frequency입니다.\n"
            "가능한 값: '3M', '6M', '1Y'"
        )


# ============================================================
# 2. Call Date 생성
# ============================================================

def generate_call_dates(
    trade_date,
    maturity,
    valuation_date,
    frequency="1Y"
):

    """
    trade_date부터 maturity까지 정기적인 Call Date를 생성합니다.

    Parameters
    ----------
    trade_date : ql.Date
        거래일.

        중요:
        trade_date 자체는 Call Date가 될 수 없습니다.

    maturity : ql.Date
        만기일.

        중요:
        maturity 자체는 Call Date가 될 수 없습니다.

    valuation_date : ql.Date
        평가일.

        valuation_date 이전 또는 당일의 Call Date는 제외됩니다.

    frequency : str
        Call 주기.

        지원:
            "3M"
            "6M"
            "1Y"

    Returns
    -------
    list[ql.Date]

        Call Date 목록


    예시
    ----
    trade_date =
        2025-01-01

    valuation_date =
        2026-08-29

    maturity =
        2036-01-01

    frequency =
        "1Y"

    생성 후보:

        2026-01-01
        2027-01-01
        2028-01-01
        ...
        2036-01-01

    여기서

        2026-01-01
            → valuation_date 이전이므로 제외

        2027-01-01
        ...
        2035-01-01
            → 포함

        2036-01-01
            → maturity이므로 제외

    결과:

        2027-01-01
        2028-01-01
        ...
        2035-01-01
    """

    # ========================================================
    # 1. Type Check
    # ========================================================

    if not isinstance(
        trade_date,
        ql.Date
    ):

        raise TypeError(
            "trade_date는 QuantLib ql.Date여야 합니다."
        )


    if not isinstance(
        maturity,
        ql.Date
    ):

        raise TypeError(
            "maturity는 QuantLib ql.Date여야 합니다."
        )


    if not isinstance(
        valuation_date,
        ql.Date
    ):

        raise TypeError(
            "valuation_date는 QuantLib ql.Date여야 합니다."
        )


    # ========================================================
    # 2. Date 순서 확인
    # ========================================================

    if trade_date >= maturity:

        raise ValueError(
            "trade_date는 maturity보다 빨라야 합니다.\n"
            f"trade_date = {trade_date}\n"
            f"maturity   = {maturity}"
        )


    # ========================================================
    # 3. Frequency
    # ========================================================

    period = _frequency_to_period(
        frequency
    )


    # ========================================================
    # 4. Call Date 생성
    # ========================================================

    call_dates = []


    # --------------------------------------------------------
    # 첫 번째 후보
    #
    # trade_date 자체는 Call Date가 아니므로
    # 반드시 trade_date + frequency부터 시작
    # --------------------------------------------------------

    current_date = (
        trade_date
        + period
    )


    # ========================================================
    # 5. trade_date ~ maturity
    # ========================================================

    while current_date < maturity:

        # ----------------------------------------------------
        # valuation_date 이후인 경우만 포함
        #
        # valuation_date 당일은 제외
        # ----------------------------------------------------

        if current_date > valuation_date:

            # ------------------------------------------------
            # trade_date는 원천적으로 제외되어 있음
            #
            # maturity도 while 조건으로 제외됨
            # ------------------------------------------------

            call_dates.append(
                current_date
            )

        # ----------------------------------------------------
        # 다음 Call Date
        # ----------------------------------------------------

        current_date = (
            current_date
            + period
        )


    # ========================================================
    # 6. 중복 제거
    # ========================================================

    unique_call_dates = []

    for date in call_dates:

        if date not in unique_call_dates:

            unique_call_dates.append(
                date
            )


    # ========================================================
    # 7. 날짜 정렬
    # ========================================================

    unique_call_dates.sort()


    # ========================================================
    # 8. Return
    # ========================================================

    return unique_call_dates


# ============================================================
# 3. Call Date 출력 함수
# ============================================================

def print_call_dates(
    call_dates
):

    """
    Call Date 목록을 보기 좋게 출력합니다.
    """

    print()
    print("=" * 70)
    print("Callable Instrument Call Dates")
    print("=" * 70)
    print()

    if not call_dates:

        print(
            "생성된 Call Date가 없습니다."
        )

        print()

        return


    print(
        f"Number of Call Dates : "
        f"{len(call_dates)}"
    )

    print()

    for i, date in enumerate(
        call_dates,
        start=1
    ):

        print(
            f"{i:>3}. {date}"
        )

    print()