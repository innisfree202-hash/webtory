# ============================================================
# CallableCMS.py
#
# Callable CMS Swap
#
# G2++ Exact Monte Carlo
# + Bermudan Cancellation Right
# + Longstaff-Schwartz
#
# 핵심 수정사항
# ------------------------------------------------------------
# 1. 모든 3M CMS coupon을 pathwise cashflow로 먼저 생성
# 2. Call date 사이의 coupon을 LSM continuation value에 포함
# 3. First call 이전 coupon을 Callable PV에 포함
# 4. Last call 이후 coupon도 realized pathwise cashflow로 평가
# 5. LSM exercise decision은 현재 call-date state (x,y)에 대한
#    조건부 continuation estimate로 수행
# 6. 실제 valuation에는 regression estimate가 아니라
#    realized continuation sample을 사용
# 7. Exact G2++ path discount factor 사용
#
# 기존 외부 인터페이스 유지:
#
# price_callable_cms_swap(...)
# print_callable_cms_swap_result(...)
#
# ============================================================

import QuantLib as ql
import numpy as np
import pandas as pd


# ============================================================
# 1. Schedule
# ============================================================

def _build_schedule(
    start_date,
    maturity_date,
    tenor="3M"
):
    calendar = ql.TARGET()

    return ql.Schedule(
        start_date,
        maturity_date,
        ql.Period(str(tenor).strip().upper()),
        calendar,
        ql.ModifiedFollowing,
        ql.ModifiedFollowing,
        ql.DateGeneration.Forward,
        False
    )


# ============================================================
# 2. Date -> Simulation Index
# ============================================================

def _date_to_simulation_index(
    date,
    valuation_date,
    dt,
    day_counter
):
    t = day_counter.yearFraction(
        valuation_date,
        date
    )

    return int(
        round(t / dt)
    )


# ============================================================
# 3. G2 B Function
#
# B(k;t,T) = (1-exp(-k(T-t))) / k
# ============================================================

def _g2_B(
    mean_reversion,
    t,
    T
):
    tau = float(T - t)

    if tau <= 0.0:
        return 0.0

    if abs(mean_reversion) < 1.0e-12:
        return tau

    return (
        1.0
        - np.exp(
            -mean_reversion * tau
        )
    ) / mean_reversion


# ============================================================
# 4. G2 Conditional Bond Cache
#
# P(t,T|x,y)
# =
# P_model(t,T|0,0)
# * exp(-B_a*x - B_b*y)
# ============================================================

class _G2BondCache:

    def __init__(
        self,
        model
    ):
        self.model = model

        params = model.params()

        self.a = float(params[0])
        self.sigma = float(params[1])
        self.b = float(params[2])
        self.eta = float(params[3])
        self.rho = float(params[4])

        self.cache = {}


    def _zero_state_bond(
        self,
        t,
        T
    ):
        t = float(t)
        T = float(T)

        if T <= t:
            return 1.0

        key = (
            round(t, 12),
            round(T, 12)
        )

        if key in self.cache:
            return self.cache[key]

        factors = ql.Array(
            [0.0, 0.0]
        )

        try:
            value = float(
                self.model.discountBond(
                    t,
                    T,
                    factors
                )
            )

        except TypeError:
            # QuantLib wrapper 버전에 따른 fallback
            value = float(
                self.model.discountBond(
                    t,
                    T,
                    0.0,
                    0.0
                )
            )

        self.cache[key] = value

        return value


    def bonds(
        self,
        t,
        T,
        x,
        y
    ):
        x = np.asarray(
            x,
            dtype=float
        )

        y = np.asarray(
            y,
            dtype=float
        )

        t = float(t)
        T = float(T)

        if T <= t:
            return np.ones_like(x)

        bx = _g2_B(
            self.a,
            t,
            T
        )

        by = _g2_B(
            self.b,
            t,
            T
        )

        zero_state_bond = (
            self._zero_state_bond(
                t,
                T
            )
        )

        return (
            zero_state_bond
            * np.exp(
                -bx * x
                -by * y
            )
        )


# ============================================================
# 5. CMS Underlying Swap Schedule Cache
#
# CMS fixing/reset date마다
# floating_tenor 길이의 underlying swap schedule 생성.
#
# 예:
# reset = 2028-01-01
# CMS tenor = 5Y
#
# underlying fixed leg:
# annual payments
# ============================================================

def _build_cms_schedule_cache(
    reset_dates,
    floating_tenor,
    fixed_payment_tenor="1Y"
):
    calendar = ql.TARGET()

    cache = {}

    for reset_date in reset_dates:

        key = reset_date.serialNumber()

        if key in cache:
            continue

        cms_maturity = calendar.advance(
            reset_date,
            ql.Period(
                str(floating_tenor)
                .strip()
                .upper()
            ),
            ql.ModifiedFollowing
        )

        schedule = ql.Schedule(
            reset_date,
            cms_maturity,
            ql.Period(
                str(fixed_payment_tenor)
                .strip()
                .upper()
            ),
            calendar,
            ql.ModifiedFollowing,
            ql.ModifiedFollowing,
            ql.DateGeneration.Forward,
            False
        )

        cache[key] = list(
            schedule
        )

    return cache


# ============================================================
# 6. Vectorized G2 CMS Rate
#
# CMS Rate
# =
# [P(t,T0)-P(t,Tn)]
# -------------------
# Σ alpha_i P(t,Ti)
#
# Reset date에서는 P(t,T0)=1
# ============================================================

def _g2_cms_rate_vectorized(
    bond_cache,
    t,
    reset_date,
    cms_schedule,
    x,
    y,
    day_counter
):
    x = np.asarray(
        x,
        dtype=float
    )

    y = np.asarray(
        y,
        dtype=float
    )

    dates = cms_schedule

    if len(dates) < 2:
        return np.zeros_like(x)

    final_date = dates[-1]

    maturity_time = (
        day_counter.yearFraction(
            reset_date,
            final_date
        )
    )

    T_end = (
        float(t)
        + maturity_time
    )

    p_end = bond_cache.bonds(
        t,
        T_end,
        x,
        y
    )

    annuity = np.zeros_like(
        x
    )

    for i in range(
        1,
        len(dates)
    ):
        previous_date = dates[
            i - 1
        ]

        payment_date = dates[i]

        accrual = (
            day_counter.yearFraction(
                previous_date,
                payment_date
            )
        )

        maturity_time = (
            day_counter.yearFraction(
                reset_date,
                payment_date
            )
        )

        T_payment = (
            float(t)
            + maturity_time
        )

        payment_df = (
            bond_cache.bonds(
                t,
                T_payment,
                x,
                y
            )
        )

        annuity += (
            accrual
            * payment_df
        )

    result = np.zeros_like(
        x
    )

    valid = (
        annuity > 1.0e-16
    )

    result[valid] = (
        1.0
        - p_end[valid]
    ) / annuity[valid]

    return result


# ============================================================
# 7. Coupon Information
#
# payment_tenor 기본값 = 3M
#
# 각 coupon:
# reset_date
# payment_date
# reset_index
# payment_index
# accrual
# ============================================================

def _build_coupon_info(
    valuation_date,
    maturity,
    payment_tenor,
    dt,
    day_counter
):
    schedule = _build_schedule(
        valuation_date,
        maturity,
        payment_tenor
    )

    dates = list(
        schedule
    )

    result = []

    for i in range(
        1,
        len(dates)
    ):
        reset_date = dates[
            i - 1
        ]

        payment_date = dates[i]

        reset_index = (
            _date_to_simulation_index(
                reset_date,
                valuation_date,
                dt,
                day_counter
            )
        )

        payment_index = (
            _date_to_simulation_index(
                payment_date,
                valuation_date,
                dt,
                day_counter
            )
        )

        accrual = (
            day_counter.yearFraction(
                reset_date,
                payment_date
            )
        )

        result.append({
            "reset_date":
                reset_date,

            "payment_date":
                payment_date,

            "reset_index":
                reset_index,

            "payment_index":
                payment_index,

            "accrual":
                float(accrual)
        })

    return result


# ============================================================
# 8. Build Pathwise CMS Cashflows
#
# 이번 수정의 핵심.
#
# 모든 coupon을 미리 pathwise로 생성한다.
#
# PAYER:
# CF = N * accrual * (CMS + spread - fixed)
#
# RECEIVER:
# CF = N * accrual * (fixed - CMS - spread)
#
# return:
#
# list of
# {
#   reset_date,
#   payment_date,
#   reset_index,
#   payment_index,
#   accrual,
#   cms_rate,
#   cashflow
# }
#
# cashflow shape = (n_paths,)
# ============================================================

def _build_pathwise_cms_cashflows(
    simulation,
    valuation_date,
    maturity,
    fixed_rate,
    floating_spread,
    notional,
    floating_tenor,
    receiver_payer,
    payment_tenor="3M"
):
    x_paths = np.asarray(
        simulation["x"],
        dtype=float
    )

    y_paths = np.asarray(
        simulation["y"],
        dtype=float
    )

    times = np.asarray(
        simulation["times"],
        dtype=float
    )

    discount_factors = np.asarray(
        simulation["discount_factors"],
        dtype=float
    )

    model = simulation["model"]

    dt = float(
        simulation["dt"]
    )

    n_paths = x_paths.shape[0]

    if x_paths.shape != y_paths.shape:
        raise ValueError(
            "simulation x/y path shape가 다릅니다."
        )

    if discount_factors.shape != x_paths.shape:
        raise ValueError(
            "simulation['discount_factors']와 "
            "x/y path shape가 다릅니다."
        )

    day_counter = (
        ql.Actual365Fixed()
    )

    coupon_info = (
        _build_coupon_info(
            valuation_date,
            maturity,
            payment_tenor,
            dt,
            day_counter
        )
    )

    reset_dates = [
        item["reset_date"]
        for item in coupon_info
    ]

    cms_schedule_cache = (
        _build_cms_schedule_cache(
            reset_dates,
            floating_tenor,
            "1Y"
        )
    )

    bond_cache = (
        _G2BondCache(
            model
        )
    )

    cashflow_records = []

    for item in coupon_info:

        reset_date = (
            item["reset_date"]
        )

        payment_date = (
            item["payment_date"]
        )

        reset_index = int(
            item["reset_index"]
        )

        payment_index = int(
            item["payment_index"]
        )

        accrual = float(
            item["accrual"]
        )

        if reset_index < 0:
            continue

        if reset_index >= len(times):
            continue

        if payment_index <= 0:
            continue

        if payment_index >= len(times):
            continue

        t = float(
            times[
                reset_index
            ]
        )

        x = x_paths[
            :,
            reset_index
        ]

        y = y_paths[
            :,
            reset_index
        ]

        cms_schedule = (
            cms_schedule_cache[
                reset_date.serialNumber()
            ]
        )

        cms_rate = (
            _g2_cms_rate_vectorized(
                bond_cache=
                    bond_cache,

                t=
                    t,

                reset_date=
                    reset_date,

                cms_schedule=
                    cms_schedule,

                x=
                    x,

                y=
                    y,

                day_counter=
                    day_counter
            )
        )

        floating_rate = (
            cms_rate
            + floating_spread
        )

        if receiver_payer == "RECEIVER":

            cashflow_rate = (
                fixed_rate
                - floating_rate
            )

        elif receiver_payer == "PAYER":

            cashflow_rate = (
                floating_rate
                - fixed_rate
            )

        else:

            raise ValueError(
                "receiver_payer는 "
                "'RECEIVER' 또는 'PAYER'여야 합니다."
            )

        cashflow = (
            cashflow_rate
            * accrual
            * notional
        )

        if cashflow.shape != (
            n_paths,
        ):
            raise RuntimeError(
                "pathwise cashflow shape가 잘못되었습니다."
            )

        cashflow_records.append({

            "reset_date":
                reset_date,

            "payment_date":
                payment_date,

            "reset_index":
                reset_index,

            "payment_index":
                payment_index,

            "accrual":
                accrual,

            "cms_rate":
                cms_rate,

            "cashflow":
                cashflow

        })

    return cashflow_records


# ============================================================
# 9. Straight CMS Swap from Pathwise Cashflows
# ============================================================

def _price_straight_from_cashflows(
    cashflow_records,
    discount_factors,
    n_paths
):
    path_values = np.zeros(
        n_paths,
        dtype=float
    )

    for record in cashflow_records:

        payment_index = int(
            record["payment_index"]
        )

        cashflow = np.asarray(
            record["cashflow"],
            dtype=float
        )

        payment_df = (
            discount_factors[
                :,
                payment_index
            ]
        )

        path_values += (
            cashflow
            * payment_df
        )

    price = float(
        np.mean(
            path_values
        )
    )

    if n_paths > 1:

        standard_error = float(
            np.std(
                path_values,
                ddof=1
            )
            / np.sqrt(
                n_paths
            )
        )

    else:

        standard_error = 0.0

    return (
        price,
        standard_error,
        path_values
    )


# ============================================================
# 10. Straight CMS Swap Wrapper
#
# 기존 내부 함수명과 return 형식을 최대한 유지
# ============================================================

def _price_straight_cms_swap_g2(
    simulation,
    valuation_date,
    maturity,
    fixed_rate,
    floating_spread,
    notional,
    floating_tenor,
    receiver_payer,
    payment_tenor="3M"
):
    x_paths = np.asarray(
        simulation["x"],
        dtype=float
    )

    short_rates = np.asarray(
        simulation["short_rate"],
        dtype=float
    )

    discount_factors = np.asarray(
        simulation["discount_factors"],
        dtype=float
    )

    n_paths = x_paths.shape[0]

    cashflow_records = (
        _build_pathwise_cms_cashflows(
            simulation=
                simulation,

            valuation_date=
                valuation_date,

            maturity=
                maturity,

            fixed_rate=
                fixed_rate,

            floating_spread=
                floating_spread,

            notional=
                notional,

            floating_tenor=
                floating_tenor,

            receiver_payer=
                receiver_payer,

            payment_tenor=
                payment_tenor
        )
    )

    (
        price,
        standard_error,
        path_values
    ) = _price_straight_from_cashflows(
        cashflow_records,
        discount_factors,
        n_paths
    )

    return (
        price,
        standard_error,
        path_values,
        short_rates,
        discount_factors
    )


# ============================================================
# 11. Deterministic Zero Curve CMS Rate
# ============================================================

def _zero_curve_cms_rate(
    curve,
    reset_date,
    floating_tenor
):
    calendar = ql.TARGET()

    day_counter = (
        ql.Actual365Fixed()
    )

    cms_maturity = (
        calendar.advance(
            reset_date,
            ql.Period(
                floating_tenor
            ),
            ql.ModifiedFollowing
        )
    )

    schedule = ql.Schedule(
        reset_date,
        cms_maturity,
        ql.Period("1Y"),
        calendar,
        ql.ModifiedFollowing,
        ql.ModifiedFollowing,
        ql.DateGeneration.Forward,
        False
    )

    dates = list(
        schedule
    )

    if len(dates) < 2:
        return 0.0

    p_start = float(
        curve.discount(
            reset_date
        )
    )

    p_end = float(
        curve.discount(
            dates[-1]
        )
    )

    annuity = 0.0

    for i in range(
        1,
        len(dates)
    ):
        previous_date = (
            dates[
                i - 1
            ]
        )

        payment_date = (
            dates[i]
        )

        accrual = (
            day_counter.yearFraction(
                previous_date,
                payment_date
            )
        )

        df = float(
            curve.discount(
                payment_date
            )
        )

        annuity += (
            accrual
            * df
        )

    if annuity <= 1.0e-16:
        return 0.0

    return (
        p_start
        - p_end
    ) / annuity


# ============================================================
# 12. Deterministic Zero Curve CMS Swap
# ============================================================

def _price_zero_curve_cms_swap(
    simulation,
    valuation_date,
    maturity,
    fixed_rate,
    floating_spread,
    notional,
    floating_tenor,
    receiver_payer,
    payment_tenor="3M"
):
    curve = simulation[
        "curve"
    ]

    day_counter = (
        ql.Actual365Fixed()
    )

    schedule = (
        _build_schedule(
            valuation_date,
            maturity,
            payment_tenor
        )
    )

    dates = list(
        schedule
    )

    fixed_pv = 0.0
    floating_pv = 0.0
    annuity = 0.0

    cms_rate_cache = {}

    for i in range(
        1,
        len(dates)
    ):
        reset_date = (
            dates[
                i - 1
            ]
        )

        payment_date = (
            dates[i]
        )

        accrual = (
            day_counter.yearFraction(
                reset_date,
                payment_date
            )
        )

        payment_df = float(
            curve.discount(
                payment_date
            )
        )

        key = (
            reset_date.serialNumber(),
            floating_tenor
        )

        if key not in cms_rate_cache:

            cms_rate_cache[key] = (
                _zero_curve_cms_rate(
                    curve,
                    reset_date,
                    floating_tenor
                )
            )

        cms_rate = (
            cms_rate_cache[key]
        )

        floating_rate = (
            cms_rate
            + floating_spread
        )

        fixed_pv += (
            fixed_rate
            * accrual
            * notional
            * payment_df
        )

        floating_pv += (
            floating_rate
            * accrual
            * notional
            * payment_df
        )

        annuity += (
            accrual
            * notional
            * payment_df
        )

    if receiver_payer == "RECEIVER":

        pv = (
            fixed_pv
            - floating_pv
        )

    elif receiver_payer == "PAYER":

        pv = (
            floating_pv
            - fixed_pv
        )

    else:

        raise ValueError(
            "receiver_payer는 "
            "'RECEIVER' 또는 'PAYER'여야 합니다."
        )

    return (
        float(pv),
        float(fixed_pv),
        float(floating_pv),
        float(annuity)
    )


# ============================================================
# 13. LSM Regression
#
# Basis:
# 1, x, y, x^2, xy, y^2
# ============================================================

def _lsm_regression(
    x,
    y,
    continuation_value
):
    x = np.asarray(
        x,
        dtype=float
    )

    y = np.asarray(
        y,
        dtype=float
    )

    continuation_value = np.asarray(
        continuation_value,
        dtype=float
    )

    n = len(x)

    if n == 0:

        return np.array(
            [],
            dtype=float
        )

    if n < 6:

        return np.full(
            n,
            float(
                np.mean(
                    continuation_value
                )
            )
        )

    A = np.empty(
        (
            n,
            6
        ),
        dtype=float
    )

    A[:, 0] = 1.0
    A[:, 1] = x
    A[:, 2] = y
    A[:, 3] = x * x
    A[:, 4] = x * y
    A[:, 5] = y * y

    coefficients = (
        np.linalg.lstsq(
            A,
            continuation_value,
            rcond=None
        )[0]
    )

    return (
        A
        @ coefficients
    )


# ============================================================
# 14. Discounted Pathwise Cashflows Between Dates
#
# value at from_index:
#
# Σ CF_j * D(from,payment_j)
#
# D(from,payment)
# =
# D(0,payment) / D(0,from)
#
# Boundary convention:
#
# from_date < payment_date <= to_date
#
# 즉 call date 당일 지급 coupon이 있다면
# coupon 지급 후 call exercise를 한다고 가정.
# ============================================================

def _cashflows_between_dates_at_index(
    cashflow_records,
    discount_factors,
    from_date,
    from_index,
    to_date,
    n_paths
):
    values = np.zeros(
        n_paths,
        dtype=float
    )

    from_df = (
        discount_factors[
            :,
            from_index
        ]
    )

    for record in cashflow_records:

        payment_date = (
            record["payment_date"]
        )

        if payment_date <= from_date:
            continue

        if payment_date > to_date:
            continue

        payment_index = int(
            record["payment_index"]
        )

        payment_df = (
            discount_factors[
                :,
                payment_index
            ]
        )

        df_from_to_payment = (
            np.divide(
                payment_df,
                from_df,
                out=np.zeros_like(
                    payment_df
                ),
                where=np.abs(
                    from_df
                ) > 1.0e-300
            )
        )

        values += (
            np.asarray(
                record["cashflow"],
                dtype=float
            )
            * df_from_to_payment
        )

    return values


# ============================================================
# 15. Cashflows after Date until Maturity
# ============================================================

def _cashflows_after_date_at_index(
    cashflow_records,
    discount_factors,
    from_date,
    from_index,
    n_paths
):
    values = np.zeros(
        n_paths,
        dtype=float
    )

    from_df = (
        discount_factors[
            :,
            from_index
        ]
    )

    for record in cashflow_records:

        payment_date = (
            record["payment_date"]
        )

        if payment_date <= from_date:
            continue

        payment_index = int(
            record["payment_index"]
        )

        payment_df = (
            discount_factors[
                :,
                payment_index
            ]
        )

        df_from_to_payment = (
            np.divide(
                payment_df,
                from_df,
                out=np.zeros_like(
                    payment_df
                ),
                where=np.abs(
                    from_df
                ) > 1.0e-300
            )
        )

        values += (
            np.asarray(
                record["cashflow"],
                dtype=float
            )
            * df_from_to_payment
        )

    return values


# ============================================================
# 16. Cashflows from Valuation Date through First Call
#
# PV at valuation date.
#
# Boundary:
# payment_date <= first_call_date
#
# same-day coupon is paid before call exercise.
# ============================================================

def _cashflows_before_or_on_first_call_pv(
    cashflow_records,
    discount_factors,
    first_call_date,
    n_paths
):
    values = np.zeros(
        n_paths,
        dtype=float
    )

    for record in cashflow_records:

        payment_date = (
            record["payment_date"]
        )

        if payment_date > first_call_date:
            continue

        payment_index = int(
            record["payment_index"]
        )

        values += (
            np.asarray(
                record["cashflow"],
                dtype=float
            )
            * discount_factors[
                :,
                payment_index
            ]
        )

    return values


# ============================================================
# 17. Main
# ============================================================

def price_callable_cms_swap(
    simulation,
    trade_date,
    valuation_date,
    maturity,
    fixed_rate,
    floating_spread=0.0,
    notional=1.0,
    floating_tenor="1Y",
    call_exists=True,
    call_dates=None,
    receiver_payer="PAYER",
    call_right="PAYER",
    payment_tenor="3M"
):
    """
    Callable CMS Swap valuation.

    receiver_payer
    ----------------------------------------------------------
    PAYER
        Pay Fixed / Receive CMS

    RECEIVER
        Receive Fixed / Pay CMS


    call_right
    ----------------------------------------------------------
    PAYER
        Payer-position holder has cancellation right

    RECEIVER
        Receiver-position holder has cancellation right


    Exercise payoff
    ----------------------------------------------------------
    Cancellation value = 0.

    Holder-perspective estimated continuation value < 0 이면
    terminate가 유리하다고 판단한다.


    Cashflow timing assumption
    ----------------------------------------------------------
    coupon이 call date와 같은 날이면 coupon 지급 후 call 행사.
    """

    # ========================================================
    # A. Validation
    # ========================================================

    if not isinstance(
        trade_date,
        ql.Date
    ):
        raise TypeError(
            "trade_date는 QuantLib ql.Date여야 합니다."
        )

    if not isinstance(
        valuation_date,
        ql.Date
    ):
        raise TypeError(
            "valuation_date는 QuantLib ql.Date여야 합니다."
        )

    if not isinstance(
        maturity,
        ql.Date
    ):
        raise TypeError(
            "maturity는 QuantLib ql.Date여야 합니다."
        )

    required_simulation_keys = [
        "times",
        "x",
        "y",
        "short_rate",
        "discount_factors",
        "curve",
        "model",
        "today",
        "dt"
    ]

    for key in required_simulation_keys:

        if key not in simulation:

            raise ValueError(
                f"simulation에 '{key}'가 없습니다.\n"
                "Exact GSimulation.py에서 "
                "simulation을 다시 생성하세요."
            )

    simulation_today = (
        simulation["today"]
    )

    if valuation_date != simulation_today:

        raise ValueError(
            "valuation_date와 "
            "simulation['today']가 다릅니다.\n\n"
            f"valuation_date   = {valuation_date}\n"
            f"simulation today = {simulation_today}"
        )

    if trade_date > valuation_date:

        raise ValueError(
            "trade_date가 valuation_date보다 "
            "미래일 수 없습니다."
        )

    if maturity <= valuation_date:

        raise ValueError(
            "maturity는 valuation_date보다 "
            "이후여야 합니다."
        )

    if maturity <= trade_date:

        raise ValueError(
            "maturity는 trade_date보다 "
            "이후여야 합니다."
        )

    if notional <= 0.0:

        raise ValueError(
            "notional은 0보다 커야 합니다."
        )

    floating_tenor = str(
        floating_tenor
    ).strip().upper()

    valid_tenors = [
        "1Y",
        "2Y",
        "3Y",
        "4Y",
        "5Y",
        "10Y"
    ]

    if floating_tenor not in valid_tenors:

        raise ValueError(
            "floating_tenor는 "
            "'1Y','2Y','3Y','4Y','5Y','10Y' 중 "
            "하나여야 합니다."
        )

    payment_tenor = str(
        payment_tenor
    ).strip().upper()

    receiver_payer = str(
        receiver_payer
    ).strip().upper()

    if receiver_payer not in [
        "RECEIVER",
        "PAYER"
    ]:

        raise ValueError(
            "receiver_payer는 "
            "'RECEIVER' 또는 'PAYER'여야 합니다."
        )

    call_right = str(
        call_right
    ).strip().upper()

    if call_right not in [
        "RECEIVER",
        "PAYER"
    ]:

        raise ValueError(
            "call_right는 "
            "'RECEIVER' 또는 'PAYER'여야 합니다."
        )

    # ========================================================
    # B. Simulation Arrays
    # ========================================================

    x_paths = np.asarray(
        simulation["x"],
        dtype=float
    )

    y_paths = np.asarray(
        simulation["y"],
        dtype=float
    )

    times = np.asarray(
        simulation["times"],
        dtype=float
    )

    short_rates = np.asarray(
        simulation["short_rate"],
        dtype=float
    )

    discount_factors = np.asarray(
        simulation["discount_factors"],
        dtype=float
    )

    if x_paths.shape != y_paths.shape:
        raise ValueError(
            "x_paths / y_paths shape가 다릅니다."
        )

    if short_rates.shape != x_paths.shape:
        raise ValueError(
            "short_rate path shape가 x/y와 다릅니다."
        )

    if discount_factors.shape != x_paths.shape:
        raise ValueError(
            "discount_factors shape가 x/y와 다릅니다."
        )

    n_paths = x_paths.shape[0]

    dt = float(
        simulation["dt"]
    )

    day_counter = (
        ql.Actual365Fixed()
    )

    # ========================================================
    # C. Build ALL Pathwise 3M Cashflows
    # ========================================================

    cashflow_records = (
        _build_pathwise_cms_cashflows(
            simulation=
                simulation,

            valuation_date=
                valuation_date,

            maturity=
                maturity,

            fixed_rate=
                fixed_rate,

            floating_spread=
                floating_spread,

            notional=
                notional,

            floating_tenor=
                floating_tenor,

            receiver_payer=
                receiver_payer,

            payment_tenor=
                payment_tenor
        )
    )

    if len(
        cashflow_records
    ) == 0:

        raise ValueError(
            "생성된 CMS cashflow가 없습니다."
        )

    # ========================================================
    # D. Straight CMS Swap G2
    # ========================================================

    (
        g2_straight_pv,
        g2_straight_se,
        g2_path_values
    ) = _price_straight_from_cashflows(
        cashflow_records,
        discount_factors,
        n_paths
    )

    # ========================================================
    # E. Deterministic Zero Curve CMS Swap
    # ========================================================

    (
        zero_curve_pv,
        zero_fixed_pv,
        zero_floating_pv,
        zero_annuity
    ) = _price_zero_curve_cms_swap(
        simulation=
            simulation,

        valuation_date=
            valuation_date,

        maturity=
            maturity,

        fixed_rate=
            fixed_rate,

        floating_spread=
            floating_spread,

        notional=
            notional,

        floating_tenor=
            floating_tenor,

        receiver_payer=
            receiver_payer,

        payment_tenor=
            payment_tenor
    )

    # ========================================================
    # F. No Call
    # ========================================================

    if not call_exists:

        return {

            "Product":
                "CMS Swap",

            "Valuation Date":
                valuation_date,

            "Trade Date":
                trade_date,

            "Maturity":
                maturity,

            "Call Exists":
                False,

            "Call Right":
                None,

            "Call Dates":
                [],

            "Fixed Rate":
                fixed_rate,

            "Floating Spread":
                floating_spread,

            "Floating Tenor":
                floating_tenor,

            "Payment Tenor":
                payment_tenor,

            "Notional":
                notional,

            "Receiver / Payer":
                receiver_payer,

            "Zero Curve PV":
                zero_curve_pv,

            "Zero Curve Fixed PV":
                zero_fixed_pv,

            "Zero Curve Floating PV":
                zero_floating_pv,

            "Zero Curve Annuity":
                zero_annuity,

            "G2++ PV":
                g2_straight_pv,

            "G2++ PV SE":
                g2_straight_se,

            "G2++ 95% CI Lower":
                (
                    g2_straight_pv
                    - 1.96
                    * g2_straight_se
                ),

            "G2++ 95% CI Upper":
                (
                    g2_straight_pv
                    + 1.96
                    * g2_straight_se
                ),

            "G2++ - Zero Curve":
                (
                    g2_straight_pv
                    - zero_curve_pv
                ),

            "G2++ Path Values":
                g2_path_values,

            "Cashflow Records":
                cashflow_records
        }

    # ========================================================
    # G. Active Call Dates
    # ========================================================

    if call_dates is None:

        raise ValueError(
            "call_exists=True이면 "
            "call_dates가 필요합니다."
        )

    call_dates = sorted(
        call_dates
    )

    active_call_dates = []

    for date in call_dates:

        if date <= valuation_date:
            continue

        if date >= maturity:
            continue

        active_call_dates.append(
            date
        )

    call_dates = (
        active_call_dates
    )

    if len(
        call_dates
    ) == 0:

        raise ValueError(
            "valuation_date 이후, maturity 이전의 "
            "Call Date가 없습니다."
        )

    # ========================================================
    # H. Call Indices
    # ========================================================

    call_indices = np.array(
        [
            _date_to_simulation_index(
                date,
                valuation_date,
                dt,
                day_counter
            )
            for date in call_dates
        ],
        dtype=int
    )

    if np.any(
        call_indices <= 0
    ):

        raise ValueError(
            "Call Date의 simulation index가 "
            "0 이하입니다."
        )

    if np.any(
        call_indices >= len(times)
    ):

        raise ValueError(
            "Call Date가 simulation 기간을 초과합니다."
        )

    # 같은 simulation index로 mapping되는 call date 방지
    if len(
        np.unique(
            call_indices
        )
    ) != len(
        call_indices
    ):

        raise ValueError(
            "두 개 이상의 Call Date가 동일한 "
            "simulation index로 mapping되었습니다.\n"
            "time_step_months를 더 작게 설정하세요."
        )

    # ========================================================
    # I. Holder Sign
    #
    # cashflow / value는 receiver_payer 관점
    #
    # call_right == receiver_payer -> +1
    # otherwise -> -1
    # ========================================================

    if call_right == receiver_payer:

        holder_sign = 1.0

    else:

        holder_sign = -1.0

    # ========================================================
    # J. LSM Arrays
    # ========================================================

    n_calls = len(
        call_dates
    )

    callable_values = np.zeros(
        (
            n_paths,
            n_calls
        ),
        dtype=float
    )

    continuation_samples = np.zeros(
        (
            n_paths,
            n_calls
        ),
        dtype=float
    )

    continuation_estimates = np.zeros(
        (
            n_paths,
            n_calls
        ),
        dtype=float
    )

    exercise_values = np.zeros(
        (
            n_paths,
            n_calls
        ),
        dtype=float
    )

    exercise_date_index = np.full(
        n_paths,
        -1,
        dtype=int
    )

    # ========================================================
    # K. Last Call Date
    #
    # realized continuation sample
    # =
    # last call 이후 모든 pathwise CMS coupons의
    # last-call-date value
    #
    # 그리고 x,y 상태에 regression하여
    # exercise decision을 수행
    # ========================================================

    last_k = (
        n_calls - 1
    )

    last_call_date = (
        call_dates[
            last_k
        ]
    )

    last_call_index = int(
        call_indices[
            last_k
        ]
    )

    last_continuation_sample = (
        _cashflows_after_date_at_index(
            cashflow_records=
                cashflow_records,

            discount_factors=
                discount_factors,

            from_date=
                last_call_date,

            from_index=
                last_call_index,

            n_paths=
                n_paths
        )
    )

    continuation_samples[
        :,
        last_k
    ] = (
        last_continuation_sample
    )

    x_last = (
        x_paths[
            :,
            last_call_index
        ]
    )

    y_last = (
        y_paths[
            :,
            last_call_index
        ]
    )

    last_continuation_estimate = (
        _lsm_regression(
            x_last,
            y_last,
            last_continuation_sample
        )
    )

    continuation_estimates[
        :,
        last_k
    ] = (
        last_continuation_estimate
    )

    holder_last_estimate = (
        holder_sign
        * last_continuation_estimate
    )

    exercise_now = (
        holder_last_estimate
        < 0.0
    )

    callable_values[
        :,
        last_k
    ] = np.where(
        exercise_now,
        0.0,
        last_continuation_sample
    )

    exercise_values[
        :,
        last_k
    ] = np.maximum(
        -holder_last_estimate,
        0.0
    )

    exercise_date_index[
        exercise_now
    ] = last_k

    # ========================================================
    # L. Backward Induction
    #
    # continuation sample at current call:
    #
    # 1) current call 이후 ~ next call까지의 coupons
    # +
    # 2) next call의 callable value
    #
    # 둘을 current call date로 할인한 값.
    #
    # 여기서 이번 수정 전 코드에 누락되어 있던
    # call-date 사이의 3M coupons가 포함된다.
    # ========================================================

    for k in range(
        n_calls - 2,
        -1,
        -1
    ):
        current_date = (
            call_dates[k]
        )

        next_date = (
            call_dates[
                k + 1
            ]
        )

        current_index = int(
            call_indices[k]
        )

        next_index = int(
            call_indices[
                k + 1
            ]
        )

        # ----------------------------------------------------
        # A. Coupons between current and next call
        # ----------------------------------------------------

        interval_coupon_value = (
            _cashflows_between_dates_at_index(
                cashflow_records=
                    cashflow_records,

                discount_factors=
                    discount_factors,

                from_date=
                    current_date,

                from_index=
                    current_index,

                to_date=
                    next_date,

                n_paths=
                    n_paths
            )
        )

        # ----------------------------------------------------
        # B. Next call callable value discounted to current
        # ----------------------------------------------------

        current_df = (
            discount_factors[
                :,
                current_index
            ]
        )

        next_df = (
            discount_factors[
                :,
                next_index
            ]
        )

        discount_current_to_next = (
            np.divide(
                next_df,
                current_df,
                out=np.zeros_like(
                    next_df
                ),
                where=np.abs(
                    current_df
                ) > 1.0e-300
            )
        )

        next_callable_value_at_current = (
            callable_values[
                :,
                k + 1
            ]
            * discount_current_to_next
        )

        continuation_sample = (
            interval_coupon_value
            + next_callable_value_at_current
        )

        continuation_samples[
            :,
            k
        ] = (
            continuation_sample
        )

        # ----------------------------------------------------
        # C. Regression on current state
        # ----------------------------------------------------

        x_current = (
            x_paths[
                :,
                current_index
            ]
        )

        y_current = (
            y_paths[
                :,
                current_index
            ]
        )

        continuation_estimate = (
            _lsm_regression(
                x_current,
                y_current,
                continuation_sample
            )
        )

        continuation_estimates[
            :,
            k
        ] = (
            continuation_estimate
        )

        # ----------------------------------------------------
        # D. Exercise Decision
        #
        # Cancellation payoff = 0
        #
        # exercise if holder continuation estimate < 0
        # ----------------------------------------------------

        holder_continuation_estimate = (
            holder_sign
            * continuation_estimate
        )

        exercise_now = (
            holder_continuation_estimate
            < 0.0
        )

        # ----------------------------------------------------
        # E. Realized callable value
        #
        # Exercise -> 0
        # Continue -> realized continuation sample
        #
        # regression estimate를 PV로 직접 쓰지 않는다.
        # ----------------------------------------------------

        callable_values[
            :,
            k
        ] = np.where(
            exercise_now,
            0.0,
            continuation_sample
        )

        exercise_values[
            :,
            k
        ] = np.maximum(
            -holder_continuation_estimate,
            0.0
        )

        # backward induction이므로
        # 더 빠른 exercise가 있으면 덮어쓴다.
        exercise_date_index[
            exercise_now
        ] = k

    # ========================================================
    # M. Valuation -> First Call
    #
    # 이번 수정 전 빠져 있던 first call 이전 coupons 포함.
    #
    # PV =
    #
    # PV(coupons before/on first call)
    # +
    # DF(0,first call) * callable value at first call
    # ========================================================

    first_call_date = (
        call_dates[0]
    )

    first_call_index = int(
        call_indices[0]
    )

    pre_first_call_pv = (
        _cashflows_before_or_on_first_call_pv(
            cashflow_records=
                cashflow_records,

            discount_factors=
                discount_factors,

            first_call_date=
                first_call_date,

            n_paths=
                n_paths
        )
    )

    first_callable_value = (
        callable_values[
            :,
            0
        ]
    )

    valuation_to_first_call_df = (
        discount_factors[
            :,
            first_call_index
        ]
    )

    present_values = (
        pre_first_call_pv
        + first_callable_value
        * valuation_to_first_call_df
    )

    # ========================================================
    # N. Callable Price
    # ========================================================

    callable_price = float(
        np.mean(
            present_values
        )
    )

    if n_paths > 1:

        callable_se = float(
            np.std(
                present_values,
                ddof=1
            )
            / np.sqrt(
                n_paths
            )
        )

    else:

        callable_se = 0.0

    # ========================================================
    # O. Callable Option Value
    #
    # Holder perspective:
    #
    # holder_sign
    # * (Callable - Straight)
    #
    # cancellation right holder에게 이론적으로 >= 0
    # ========================================================

    callable_option_value_raw = float(
        holder_sign
        * (
            callable_price
            - g2_straight_pv
        )
    )

    # ========================================================
    # P. Exercise Statistics
    # ========================================================

    exercise_statistics = []

    for k, date in enumerate(
        call_dates
    ):
        count = int(
            np.sum(
                exercise_date_index
                == k
            )
        )

        percentage = (
            count
            / n_paths
            * 100.0
        )

        exercise_statistics.append({

            "Call Date":
                date,

            "Exercise Paths":
                count,

            "Exercise %":
                percentage
        })

    never_exercised = int(
        np.sum(
            exercise_date_index
            < 0
        )
    )

    never_exercised_percentage = (
        never_exercised
        / n_paths
        * 100.0
    )

    exercise_df = pd.DataFrame(
        exercise_statistics
    )

    # ========================================================
    # Q. Optional Cashflow Summary
    #
    # Excel/debugging에 사용할 수 있도록
    # path-average coupon 정보도 제공
    # ========================================================

    cashflow_summary_rows = []

    for record in cashflow_records:

        cashflow_array = np.asarray(
            record["cashflow"],
            dtype=float
        )

        cms_rate_array = np.asarray(
            record["cms_rate"],
            dtype=float
        )

        cashflow_summary_rows.append({

            "Reset Date":
                record["reset_date"],

            "Payment Date":
                record["payment_date"],

            "Reset Index":
                record["reset_index"],

            "Payment Index":
                record["payment_index"],

            "Accrual":
                record["accrual"],

            "Mean CMS Rate":
                float(
                    np.mean(
                        cms_rate_array
                    )
                ),

            "Mean Cashflow":
                float(
                    np.mean(
                        cashflow_array
                    )
                )
        })

    cashflow_summary = pd.DataFrame(
        cashflow_summary_rows
    )

    # ========================================================
    # R. Result
    # ========================================================

    result = {

        "Product":
            "Callable CMS Swap",

        "Valuation Date":
            valuation_date,

        "Trade Date":
            trade_date,

        "Maturity":
            maturity,

        "Call Exists":
            True,

        "Call Right":
            call_right,

        "Call Dates":
            call_dates,

        "Call Indices":
            call_indices,

        "Fixed Rate":
            fixed_rate,

        "Floating Spread":
            floating_spread,

        "Floating Tenor":
            floating_tenor,

        "Payment Tenor":
            payment_tenor,

        "Notional":
            notional,

        "Receiver / Payer":
            receiver_payer,

        "Holder Sign":
            holder_sign,

        # ----------------------------------------------------
        # Deterministic Zero Curve
        # ----------------------------------------------------

        "Zero Curve PV":
            zero_curve_pv,

        "Zero Curve Fixed PV":
            zero_fixed_pv,

        "Zero Curve Floating PV":
            zero_floating_pv,

        "Zero Curve Annuity":
            zero_annuity,

        # ----------------------------------------------------
        # Straight G2
        # ----------------------------------------------------

        "G2++ Straight Swap PV":
            g2_straight_pv,

        "G2++ Straight Swap SE":
            g2_straight_se,

        "G2++ Straight 95% CI Lower":
            (
                g2_straight_pv
                - 1.96
                * g2_straight_se
            ),

        "G2++ Straight 95% CI Upper":
            (
                g2_straight_pv
                + 1.96
                * g2_straight_se
            ),

        # ----------------------------------------------------
        # Callable
        # ----------------------------------------------------

        "Callable CMS Swap PV":
            callable_price,

        "Callable CMS Swap SE":
            callable_se,

        "Callable 95% CI Lower":
            (
                callable_price
                - 1.96
                * callable_se
            ),

        "Callable 95% CI Upper":
            (
                callable_price
                + 1.96
                * callable_se
            ),

        # ----------------------------------------------------
        # Option
        # ----------------------------------------------------

        "Callable Option Value":
            callable_option_value_raw,

        # ----------------------------------------------------
        # Comparison
        # ----------------------------------------------------

        "G2++ - Zero Curve":
            (
                g2_straight_pv
                - zero_curve_pv
            ),

        # ----------------------------------------------------
        # Exercise
        # ----------------------------------------------------

        "Exercise Statistics":
            exercise_df,

        "Never Exercised Paths":
            never_exercised,

        "Never Exercised %":
            never_exercised_percentage,

        # ----------------------------------------------------
        # Cashflow
        # ----------------------------------------------------

        "Cashflow Summary":
            cashflow_summary,

        "Cashflow Records":
            cashflow_records,

        # ----------------------------------------------------
        # Detailed Arrays
        # ----------------------------------------------------

        "Path Values":
            present_values,

        "Straight Path Values":
            g2_path_values,

        "Pre First Call PV":
            pre_first_call_pv,

        "Exercise Date Index":
            exercise_date_index,

        "Exercise Values":
            exercise_values,

        "Continuation Samples":
            continuation_samples,

        "Continuation Estimates":
            continuation_estimates,

        "Callable Values":
            callable_values,

        "X Paths":
            x_paths,

        "Y Paths":
            y_paths,

        "Short Rate Paths":
            short_rates,

        "Discount Factors":
            discount_factors
    }

    return result


# ============================================================
# 18. Print Result
# ============================================================

def print_callable_cms_swap_result(
    result
):
    print()

    print(
        "=" * 100
    )

    if result[
        "Call Exists"
    ]:

        print(
            "Callable CMS Swap"
        )

    else:

        print(
            "Plain CMS Swap"
        )

    print(
        "=" * 100
    )

    print()

    print(
        f"Valuation Date       : "
        f"{result['Valuation Date']}"
    )

    print(
        f"Trade Date           : "
        f"{result['Trade Date']}"
    )

    print(
        f"Maturity             : "
        f"{result['Maturity']}"
    )

    print()

    print(
        f"Fixed Rate           : "
        f"{result['Fixed Rate']:.6%}"
    )

    print(
        f"Floating Spread      : "
        f"{result['Floating Spread']:.6%}"
    )

    print(
        f"CMS Tenor            : "
        f"{result['Floating Tenor']}"
    )

    print(
        f"Payment Tenor        : "
        f"{result['Payment Tenor']}"
    )

    print(
        f"Notional             : "
        f"{result['Notional']:,.2f}"
    )

    print(
        f"Receiver / Payer     : "
        f"{result['Receiver / Payer']}"
    )

    print()

    print(
        f"Zero Curve PV        : "
        f"{result['Zero Curve PV']:,.6f}"
    )

    print()

    if result[
        "Call Exists"
    ]:

        print(
            f"G2++ Straight PV     : "
            f"{result['G2++ Straight Swap PV']:,.6f}"
        )

        print(
            f"G2++ Straight SE     : "
            f"{result['G2++ Straight Swap SE']:,.6f}"
        )

        print()

        print(
            f"Callable CMS PV      : "
            f"{result['Callable CMS Swap PV']:,.6f}"
        )

        print(
            f"Callable CMS SE      : "
            f"{result['Callable CMS Swap SE']:,.6f}"
        )

        print(
            f"Callable 95% CI      : "
            f"["
            f"{result['Callable 95% CI Lower']:,.6f}, "
            f"{result['Callable 95% CI Upper']:,.6f}"
            f"]"
        )

        print()

        print(
            f"Callable Option Value: "
            f"{result['Callable Option Value']:,.6f}"
        )

        print()

        print(
            f"Never Exercised      : "
            f"{result['Never Exercised Paths']:,} "
            f"("
            f"{result['Never Exercised %']:.4f}%"
            f")"
        )

        print()

        print(
            "Exercise Statistics"
        )

        print(
            result[
                "Exercise Statistics"
            ].to_string(
                index=False
            )
        )

    else:

        print(
            f"G2++ PV              : "
            f"{result['G2++ PV']:,.6f}"
        )

        print(
            f"G2++ PV SE           : "
            f"{result['G2++ PV SE']:,.6f}"
        )

        print(
            f"G2++ 95% CI          : "
            f"["
            f"{result['G2++ 95% CI Lower']:,.6f}, "
            f"{result['G2++ 95% CI Upper']:,.6f}"
            f"]"
        )

    print()

    print(
        "=" * 100
    )

    print()
