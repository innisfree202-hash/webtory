# -*- coding: utf-8 -*-

"""
Bootstrapping.py

KRW Yield Curve Bootstrapping

Convention
----------
Currency             : KRW
Floating Index       : KRW-CD 3M
Floating Day Count   : Actual / 365 Fixed
Fixed Day Count      : Actual / 365 Fixed
Curve Day Count      : Actual / 365 Fixed
Calendar             : South Korea

Input Excel Columns
-------------------
Data | Maturity | yield | product

Example
-------
20250102 | 3M  | 0.0320 | DEPOSIT
20250102 | 1Y  | 0.0315 | SWAP
20250102 | 2Y  | 0.0310 | SWAP
...
"""


import QuantLib as ql
import pandas as pd


# ============================================================
# 1. Date Conversion
# ============================================================

def _to_ql_date(
    calibration_date
):

    calibration_date = str(
        calibration_date
    ).strip()

    # Excel 숫자형 날짜 대응
    calibration_date = (
        calibration_date
        .replace(".0", "")
    )

    if (
        len(calibration_date) != 8
        or
        not calibration_date.isdigit()
    ):

        raise ValueError(
            "calibration_date는 YYYYMMDD 형식이어야 합니다. "
            f"입력값: {calibration_date}"
        )


    year = int(
        calibration_date[0:4]
    )

    month = int(
        calibration_date[4:6]
    )

    day = int(
        calibration_date[6:8]
    )


    return ql.Date(
        day,
        month,
        year
    )


# ============================================================
# 2. Fixed Leg Tenor -> Frequency
# ============================================================

def _get_fixed_frequency(
    fixed_leg_tenor
):

    tenor = str(
        fixed_leg_tenor
    ).strip().upper()


    if tenor == "1M":

        return ql.Monthly


    elif tenor == "3M":

        return ql.Quarterly


    elif tenor == "6M":

        return ql.Semiannual


    elif tenor in (
        "1Y",
        "12M"
    ):

        return ql.Annual


    else:

        raise ValueError(
            "지원하지 않는 fixed_leg_tenor입니다: "
            f"{fixed_leg_tenor}\n"
            "지원값: 1M, 3M, 6M, 1Y"
        )


# ============================================================
# 3. Build KRW CD Index
# ============================================================

def _build_krw_cd_index(
    swap_index_tenor="3M"
):

    tenor_text = str(
        swap_index_tenor
    ).strip().upper()


    # --------------------------------------------------------
    # 현재 프로젝트는 KRW CD 3M만 사용
    # --------------------------------------------------------

    if tenor_text != "3M":

        raise ValueError(
            "현재 KRW Curve는 "
            "swap_index_tenor='3M'만 지원합니다."
        )


    calendar = ql.SouthKorea()


    index = ql.IborIndex(

        "KRW-CD",

        ql.Period(
            tenor_text
        ),

        1,                      # fixing days

        ql.KRWCurrency(),

        calendar,

        ql.ModifiedFollowing,

        False,                  # endOfMonth

        ql.Actual365Fixed()

    )


    return index


# ============================================================
# 4. Main Curve Bootstrapping Function
# ============================================================

def bootstrap_curve_from_excel(

    file_path,

    calibration_date,

    swap_index_tenor="3M",

    fixed_leg_tenor="3M",

    deposit_settlement_days=2,

    deposit_business_convention=ql.ModifiedFollowing,

    fixed_leg_business_convention=ql.ModifiedFollowing,

):

    """
    KRW CD / IRS market data를 이용하여
    Discount Curve를 Bootstrap 한다.

    Parameters
    ----------
    file_path
        Yield curve Excel file.

    calibration_date
        YYYYMMDD 형식의 평가일.

    swap_index_tenor
        Floating index tenor.
        현재 KRW-CD 3M만 지원.

    fixed_leg_tenor
        IRS Fixed Leg payment tenor.
        예: 3M, 6M, 1Y

    deposit_settlement_days
        Deposit settlement days.
        Default = 2

    Returns
    -------
    curve
        QuantLib YieldTermStructure

    result_df
        Market Rate / Zero Rate /
        Discount Factor 결과
    """


    # ========================================================
    # A. Calibration Date
    # ========================================================

    calibration_date_text = str(
        calibration_date
    ).strip()

    calibration_date_text = (
        calibration_date_text
        .replace(".0", "")
    )


    today = _to_ql_date(
        calibration_date_text
    )


    ql.Settings.instance().evaluationDate = (
        today
    )


    # ========================================================
    # B. Read Excel
    # ========================================================

    market_data = pd.read_excel(
        file_path
    )


    # ========================================================
    # C. Check Excel Columns
    # ========================================================

    required_columns = [

        "Data",
        "Maturity",
        "yield",
        "product"

    ]


    missing_columns = [

        col

        for col in required_columns

        if col not in market_data.columns

    ]


    if missing_columns:

        raise ValueError(

            "Excel에 다음 컬럼이 없습니다: "

            + ", ".join(
                missing_columns
            )

        )


    # ========================================================
    # D. Normalize Calibration Date
    # ========================================================

    market_data = (
        market_data.copy()
    )


    market_data["Data"] = (

        market_data["Data"]

        .astype(str)

        .str.replace(
            ".0",
            "",
            regex=False
        )

        .str.strip()

    )


    # ========================================================
    # E. Select Calibration Date
    # ========================================================

    market_data = market_data[

        market_data["Data"]
        ==
        calibration_date_text

    ].copy()


    if market_data.empty:

        raise ValueError(

            f"Excel에 {calibration_date_text} 날짜의 "
            "Yield Curve 데이터가 없습니다."

        )


    # ========================================================
    # F. Normalize Market Data
    # ========================================================

    market_data["Maturity"] = (

        market_data["Maturity"]
        .astype(str)
        .str.strip()
        .str.upper()

    )


    market_data["product"] = (

        market_data["product"]
        .astype(str)
        .str.strip()
        .str.upper()

    )


    market_data["yield"] = pd.to_numeric(

        market_data["yield"],

        errors="coerce"

    )


    # --------------------------------------------------------
    # Invalid Yield Check
    # --------------------------------------------------------

    if (
        market_data["yield"]
        .isna()
        .any()
    ):

        invalid_rows = market_data[

            market_data["yield"]
            .isna()

        ]


        raise ValueError(

            "Yield 값에 숫자가 아닌 값 또는 빈 값이 있습니다.\n\n"

            + invalid_rows.to_string(
                index=False
            )

        )


    # ========================================================
    # G. Calendar / Day Counter
    # ========================================================

    calendar = (
        ql.SouthKorea()
    )


    # Curve
    curve_day_counter = (
        ql.Actual365Fixed()
    )


    # KRW CD
    deposit_day_counter = (
        ql.Actual365Fixed()
    )


    # KRW IRS Fixed Leg
    fixed_day_counter = (
        ql.Actual365Fixed()
    )


    # ========================================================
    # H. Floating Index
    # ========================================================

    index = _build_krw_cd_index(

        swap_index_tenor=
            swap_index_tenor

    )


    # ========================================================
    # I. Fixed Leg Frequency
    # ========================================================

    fixed_frequency = (
        _get_fixed_frequency(

            fixed_leg_tenor

        )
    )


    # ========================================================
    # J. Create Rate Helpers
    # ========================================================

    helpers = []


    for _, row in market_data.iterrows():


        maturity = str(
            row["Maturity"]
        ).strip().upper()


        market_rate = float(
            row["yield"]
        )


        product = str(
            row["product"]
        ).strip().upper()


        # ----------------------------------------------------
        # Tenor
        # ----------------------------------------------------

        try:

            tenor = ql.Period(
                maturity
            )

        except Exception as exc:

            raise ValueError(

                "Maturity를 QuantLib Period로 "
                f"변환할 수 없습니다: {maturity}"

            ) from exc


        # ----------------------------------------------------
        # Quote
        # ----------------------------------------------------

        quote = ql.QuoteHandle(

            ql.SimpleQuote(
                market_rate
            )

        )


        # ====================================================
        # Deposit
        # ====================================================

        if product == "DEPOSIT":


            helper = ql.DepositRateHelper(

                quote,

                tenor,

                int(
                    deposit_settlement_days
                ),

                calendar,

                deposit_business_convention,

                False,

                deposit_day_counter

            )


        # ====================================================
        # Swap
        # ====================================================

        elif product == "SWAP":


            helper = ql.SwapRateHelper(

                quote,

                tenor,

                calendar,

                fixed_frequency,

                fixed_leg_business_convention,

                fixed_day_counter,

                index

            )


        # ====================================================
        # Unknown Product
        # ====================================================

        else:

            raise ValueError(

                "알 수 없는 product입니다: "
                f"{product}\n"
                "지원값: DEPOSIT, SWAP"

            )


        helpers.append(
            helper
        )


    # ========================================================
    # K. Helper Check
    # ========================================================

    if len(
        helpers
    ) == 0:

        raise ValueError(
            "생성된 RateHelper가 없습니다."
        )


    # ========================================================
    # L. Bootstrap Yield Curve
    # ========================================================

    try:

        curve = (
            ql.PiecewiseLogCubicDiscount(

                today,

                helpers,

                curve_day_counter

            )
        )


        curve.enableExtrapolation()


        # ----------------------------------------------------
        # Bootstrap 강제 수행
        #
        # Lazy evaluation이기 때문에 discount(today)를
        # 호출하여 실제 bootstrap 오류를 즉시 확인한다.
        # ----------------------------------------------------

        curve.discount(
            today
        )


    except Exception as exc:

        raise RuntimeError(

            "\nYield Curve Bootstrapping에 실패했습니다.\n\n"

            f"Calibration Date : {calibration_date_text}\n"
            f"Swap Index       : KRW-CD {swap_index_tenor}\n"
            f"Fixed Leg Tenor  : {fixed_leg_tenor}\n\n"

            f"QuantLib Error:\n{exc}"

        ) from exc


    # ========================================================
    # M. Calculate Curve Results
    # ========================================================

    curve_results = []


    for _, row in market_data.iterrows():


        maturity = str(
            row["Maturity"]
        ).strip().upper()


        product = str(
            row["product"]
        ).strip().upper()


        market_rate = float(
            row["yield"]
        )


        tenor = ql.Period(
            maturity
        )


        # ----------------------------------------------------
        # Maturity Date
        # ----------------------------------------------------

        maturity_date = (
            calendar.advance(

                today,

                tenor,

                ql.ModifiedFollowing,

                False

            )
        )


        # ----------------------------------------------------
        # Time
        # ----------------------------------------------------

        time = (
            curve_day_counter
            .yearFraction(

                today,

                maturity_date

            )
        )


        # ----------------------------------------------------
        # Zero Rate
        #
        # Continuous Compounding
        # ----------------------------------------------------

        zero_rate = (
            curve.zeroRate(

                maturity_date,

                curve_day_counter,

                ql.Continuous

            ).rate()
        )


        # ----------------------------------------------------
        # Discount Factor
        # ----------------------------------------------------

        discount_factor = (
            curve.discount(
                maturity_date
            )
        )


        # ----------------------------------------------------
        # Forward Rate
        #
        # Instantaneous-ish short interval은 사용하지 않고
        # 현재 시점부터 해당 만기까지의 simple forward를
        # 참고용으로 출력
        # ----------------------------------------------------

        if maturity_date > today:

            forward_rate = (

                curve.forwardRate(

                    today,

                    maturity_date,

                    curve_day_counter,

                    ql.Simple

                ).rate()

            )

        else:

            forward_rate = float(
                "nan"
            )


        curve_results.append({

            "Data":
                calibration_date_text,

            "Maturity":
                maturity,

            "Maturity Date":
                maturity_date.ISO(),

            "Product":
                product,

            "Market Rate":
                market_rate,

            "Time":
                time,

            "Zero Rate":
                zero_rate,

            "Forward Rate":
                forward_rate,

            "Discount Factor":
                discount_factor

        })


    # ========================================================
    # N. Result DataFrame
    # ========================================================

    result_df = pd.DataFrame(
        curve_results
    )


    # ========================================================
    # O. Curve Information
    # ========================================================

    curve_info = {

        "Calibration Date":
            calibration_date_text,

        "QL Evaluation Date":
            today,

        "Currency":
            "KRW",

        "Calendar":
            "SouthKorea",

        "Floating Index":
            f"KRW-CD {swap_index_tenor}",

        "Floating Day Counter":
            "Actual365Fixed",

        "Fixed Leg Tenor":
            str(
                fixed_leg_tenor
            ).upper(),

        "Fixed Day Counter":
            "Actual365Fixed",

        "Curve Day Counter":
            "Actual365Fixed",

        "Deposit Settlement Days":
            int(
                deposit_settlement_days
            ),

        "Number of Helpers":
            len(
                helpers
            )

    }


    # ========================================================
    # P. Return
    # ========================================================

    return (
        curve,
        result_df,
        curve_info
    )


# ============================================================
# 5. Pretty Print
# ============================================================

def print_bootstrap_curve_result(

    result_df,

    curve_info=None

):


    print()

    print(
        "=" * 110
    )

    print(
        "KRW Yield Curve Bootstrapping Result"
    )

    print(
        "=" * 110
    )


    if curve_info is not None:

        print()

        print(
            "Calibration Date :",
            curve_info.get(
                "Calibration Date"
            )
        )

        print(
            "Currency         :",
            curve_info.get(
                "Currency"
            )
        )

        print(
            "Floating Index   :",
            curve_info.get(
                "Floating Index"
            )
        )

        print(
            "Fixed Leg Tenor  :",
            curve_info.get(
                "Fixed Leg Tenor"
            )
        )

        print(
            "Curve Day Count  :",
            curve_info.get(
                "Curve Day Counter"
            )
        )

        print(
            "Number Helpers   :",
            curve_info.get(
                "Number of Helpers"
            )
        )


    print()


    display_df = (
        result_df.copy()
    )


    for col in [

        "Market Rate",
        "Zero Rate",
        "Forward Rate"

    ]:

        display_df[col] = (

            display_df[col]

            .map(
                lambda x:
                f"{x:.6%}"
            )

        )


    display_df[
        "Discount Factor"
    ] = (

        display_df[
            "Discount Factor"
        ]

        .map(
            lambda x:
            f"{x:.8f}"
        )

    )


    print(
        display_df.to_string(
            index=False
        )
    )


    print()