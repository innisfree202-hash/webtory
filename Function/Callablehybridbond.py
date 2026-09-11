# -*- coding: utf-8 -*-
"""
Callable Hybrid Bond / Hybrid Capital Security valuation under G2++ Monte Carlo.

Designed to reuse a simulation dictionary containing:
    simulation['x'], simulation['y'], simulation['times'], simulation['today']
and either simulation['short_rate'] or simulation['phi'].

Main product terms supported
----------------------------
1) Callable from a specified first call date and every call_frequency thereafter.
2) Coupon paid periodically (e.g. every 3 months).
3) Coupon is paid each period, not accumulated to maturity.
4) Coupon rule:
   - issue date through first_call_date (inclusive): initial fixed coupon;
   - after first_call_date:
       * FIXED: changed fixed coupon rate, or
       * YTM_PLUS_SPREAD: pathwise benchmark par-YTM observed at first_call_date
         for benchmark_tenor (e.g. 5Y) + reset spread.

Pricing convention
------------------
- Call decision is assumed immediately AFTER the coupon due on a call date is paid.
- Call redemption value therefore excludes that date's coupon.
- Returned price is a dirty/full price from future contractual cash flows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import QuantLib as ql


# ============================================================
# Basic helpers
# ============================================================

def _parse_period(
    value: Union[str, ql.Period]
) -> ql.Period:

    if isinstance(
        value,
        ql.Period
    ):
        return value

    s = str(
        value
    ).strip().upper()

    if len(s) < 2:

        raise ValueError(
            f"유효하지 않은 Period: {value}"
        )

    n = int(
        s[:-1]
    )

    u = s[-1]

    if u == "D":

        return ql.Period(
            n,
            ql.Days
        )

    if u == "W":

        return ql.Period(
            n,
            ql.Weeks
        )

    if u == "M":

        return ql.Period(
            n,
            ql.Months
        )

    if u == "Y":

        return ql.Period(
            n,
            ql.Years
        )

    raise ValueError(
        f"유효하지 않은 Period 단위: {value}"
    )


def _to_ql_date(
    value
) -> ql.Date:

    if isinstance(
        value,
        ql.Date
    ):
        return value

    if isinstance(
        value,
        pd.Timestamp
    ):
        value = value.to_pydatetime()

    if (
        hasattr(value, "year")
        and hasattr(value, "month")
        and hasattr(value, "day")
    ):

        return ql.Date(
            int(value.day),
            int(value.month),
            int(value.year)
        )

    if isinstance(
        value,
        str
    ):

        ts = pd.to_datetime(
            value
        )

        return ql.Date(
            int(ts.day),
            int(ts.month),
            int(ts.year)
        )

    raise TypeError(
        f"QuantLib Date로 변환할 수 없습니다: {value!r}"
    )


def _ql_date_to_timestamp(
    d: ql.Date
) -> pd.Timestamp:

    return pd.Timestamp(
        d.year(),
        d.month(),
        d.dayOfMonth()
    )


def _date_to_simulation_index(
    date: ql.Date,
    valuation_date: ql.Date,
    times: np.ndarray,
    day_counter: ql.DayCounter,
) -> int:

    t = day_counter.yearFraction(
        valuation_date,
        date
    )

    if t < -1e-12:

        raise ValueError(
            "valuation_date 이전 날짜는 "
            f"simulation index로 변환할 수 없습니다: {date}"
        )

    idx = int(
        np.argmin(
            np.abs(
                times - t
            )
        )
    )

    return idx


def _build_cumulative_discount_factors(
    short_rates: np.ndarray,
    times: np.ndarray
) -> np.ndarray:

    """
    Pathwise DF(0,t) using left-point
    short-rate integration on arbitrary time grid.
    """

    if short_rates.ndim != 2:

        raise ValueError(
            "short_rates는 2차원 배열이어야 합니다."
        )

    if short_rates.shape[1] != len(times):

        raise ValueError(
            "short_rates 시간축과 times 길이가 다릅니다."
        )

    if len(times) < 2:

        raise ValueError(
            "times 길이는 2 이상이어야 합니다."
        )

    dt = np.diff(
        times
    )

    if np.any(
        dt <= 0
    ):

        raise ValueError(
            "simulation['times']는 엄격히 증가해야 합니다."
        )

    n_paths, n_times = (
        short_rates.shape
    )

    out = np.ones(
        (
            n_paths,
            n_times
        ),
        dtype=float
    )

    increments = np.exp(
        -short_rates[:, :-1]
        *
        dt[np.newaxis, :]
    )

    out[:, 1:] = np.cumprod(
        increments,
        axis=1
    )

    return out


def _lsm_regression(
    x: np.ndarray,
    y: np.ndarray,
    continuation_value: np.ndarray
) -> np.ndarray:

    A = np.column_stack(
        [
            np.ones(len(x)),
            x,
            y,
            x ** 2,
            x * y,
            y ** 2,
        ]
    )

    if len(x) < 6:

        return np.full(
            len(x),
            float(
                np.mean(
                    continuation_value
                )
            )
        )

    coefficients = np.linalg.lstsq(
        A,
        continuation_value,
        rcond=None
    )[0]

    return (
        A
        @
        coefficients
    )


def _generate_contractual_dates(
    start_date: ql.Date,
    end_date: ql.Date,
    frequency: Union[str, ql.Period],
    include_start: bool = False,
    include_end: bool = True,
) -> List[ql.Date]:

    """
    Generate UNADJUSTED contractual dates from the original anchor date.

    Every schedule date is calculated from start_date itself, not from
    the previously generated/adjusted date. This prevents date drift.
    """

    period = _parse_period(frequency)

    step_length = period.length()
    step_units = period.units()

    if step_length <= 0:
        raise ValueError(f"frequency는 양수여야 합니다: {frequency}")

    dates = []

    if include_start:
        dates.append(start_date)

    k = 1

    while True:

        contractual_date = start_date + ql.Period(
            step_length * k,
            step_units
        )

        if contractual_date > end_date:
            break

        if contractual_date == end_date and not include_end:
            break

        dates.append(contractual_date)

        if contractual_date == end_date:
            break

        k += 1

    if include_end:
        if not dates or dates[-1] != end_date:
            dates.append(end_date)

    return dates


def _generate_dates(
    start_date: ql.Date,
    end_date: ql.Date,
    frequency: Union[str, ql.Period],
    calendar: ql.Calendar,
    business_convention=ql.ModifiedFollowing,
    include_start: bool = False,
    include_end: bool = True,
) -> List[ql.Date]:

    """
    Generate business-day-adjusted dates from an anchored contractual
    schedule. Adjustment is applied independently to every date.
    """

    contractual_dates = _generate_contractual_dates(
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        include_start=include_start,
        include_end=include_end,
    )

    adjusted_dates = []

    for contractual_date in contractual_dates:

        adjusted_date = calendar.adjust(
            contractual_date,
            business_convention
        )

        if not adjusted_dates or adjusted_dates[-1] != adjusted_date:
            adjusted_dates.append(adjusted_date)

    return adjusted_dates


def _coupon_periods(
    issue_date: ql.Date,
    maturity: ql.Date,
    coupon_frequency: Union[str, ql.Period],
    calendar: ql.Calendar,
    day_counter: ql.DayCounter,
) -> List[Tuple[ql.Date, ql.Date, ql.Date, float]]:

    """
    Return coupon periods as:

        (contractual accrual start, contractual accrual end,
         adjusted payment date, accrual factor)

    Accrual dates stay contractual/unadjusted. Only the payment date is
    business-day adjusted.
    """

    contractual_ends = _generate_contractual_dates(
        start_date=issue_date,
        end_date=maturity,
        frequency=coupon_frequency,
        include_start=False,
        include_end=True,
    )

    periods = []
    contractual_start = issue_date

    for contractual_end in contractual_ends:

        payment_date = calendar.adjust(
            contractual_end,
            ql.ModifiedFollowing
        )

        accrual = day_counter.yearFraction(
            contractual_start,
            contractual_end
        )

        periods.append((
            contractual_start,
            contractual_end,
            payment_date,
            accrual
        ))

        contractual_start = contractual_end

    return periods

def _extract_short_rates(
    simulation: dict
) -> Tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    ql.Date
]:

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

    simulation_today = (
        simulation["today"]
    )

    if x_paths.shape != y_paths.shape:

        raise ValueError(
            "x와 y simulation path shape가 다릅니다."
        )

    if (
        x_paths.ndim != 2
        or
        x_paths.shape[1] != len(times)
    ):

        raise ValueError(
            "x/y path 시간축과 "
            "simulation['times']가 일치하지 않습니다."
        )

    if "short_rate" in simulation:

        short_rates = np.asarray(
            simulation["short_rate"],
            dtype=float
        )

        if short_rates.shape != x_paths.shape:

            raise ValueError(
                "short_rate와 x/y path shape가 일치하지 않습니다."
            )

        phi = (
            short_rates
            - x_paths
            - y_paths
        ).mean(
            axis=0
        )

    elif "phi" in simulation:

        phi = np.asarray(
            simulation["phi"],
            dtype=float
        ).reshape(
            -1
        )

        if len(phi) != x_paths.shape[1]:

            raise ValueError(
                "simulation['phi'] 길이가 "
                "x/y path와 일치하지 않습니다."
            )

        short_rates = (
            x_paths
            +
            y_paths
            +
            phi[np.newaxis, :]
        )

    else:

        raise ValueError(
            "simulation에 "
            "'short_rate' 또는 'phi'가 필요합니다."
        )

    return (
        x_paths,
        y_paths,
        short_rates,
        times,
        phi,
        simulation_today
    )


# ============================================================
# Reset coupon helper: pathwise benchmark par-YTM
# ============================================================

def _benchmark_par_ytm_at_reset(
    cumulative_df: np.ndarray,
    reset_index: int,
    reset_date: ql.Date,
    benchmark_tenor: Union[str, ql.Period],
    benchmark_frequency: Union[str, ql.Period],
    valuation_date: ql.Date,
    times: np.ndarray,
    calendar: ql.Calendar,
    day_counter: ql.DayCounter,
) -> np.ndarray:

    """
    Approximate benchmark YTM as a pathwise
    par yield observed at reset_date.

    par_yield =
        (1 - P(reset,T))
        /
        sum(alpha_i * P(reset,T_i))
    """

    tenor = _parse_period(
        benchmark_tenor
    )

    end_date = calendar.advance(
        reset_date,
        tenor,
        ql.ModifiedFollowing,
        False
    )

    periods = _coupon_periods(
        reset_date,
        end_date,
        benchmark_frequency,
        calendar,
        day_counter
    )

    denom = np.zeros(
        cumulative_df.shape[0],
        dtype=float
    )

    last_df = None

    base_df = cumulative_df[
        :,
        reset_index
    ]

    for (
        start,
        end,
        pay,
        accrual
    ) in periods:

        idx = _date_to_simulation_index(
            pay,
            valuation_date,
            times,
            day_counter
        )

        if idx >= cumulative_df.shape[1]:

            raise ValueError(
                f"benchmark tenor({benchmark_tenor})가 "
                f"simulation horizon을 초과합니다: {pay}"
            )

        p_t_pay = (
            cumulative_df[
                :,
                idx
            ]
            /
            base_df
        )

        denom += (
            accrual
            *
            p_t_pay
        )

        last_df = p_t_pay

    if (
        last_df is None
        or
        np.any(
            denom <= 0
        )
    ):

        raise ValueError(
            "Benchmark YTM 계산용 할인계수를 "
            "만들 수 없습니다."
        )

    return (
        1.0
        -
        last_df
    ) / denom


# ============================================================
# Main pricer
# ============================================================

def price_callable_hybrid_bond(

    simulation: dict,

    valuation_date: ql.Date,

    issue_date: ql.Date,

    maturity: ql.Date,

    first_call_date: ql.Date,

    initial_coupon_rate: float,

    coupon_frequency: Union[
        str,
        ql.Period
    ] = "3M",

    call_frequency: Union[
        str,
        ql.Period
    ] = "3M",

    reset_rate_mode: str = "FIXED",

    reset_fixed_rate: Optional[
        float
    ] = None,

    benchmark_tenor: Union[
        str,
        ql.Period
    ] = "5Y",

    benchmark_frequency: Union[
        str,
        ql.Period
    ] = "6M",

    reset_spread: float = 0.0,

    reset_coupon_rate_override: Optional[
        float
    ] = None,

    notional: float = 10000.0,

    call_price_pct: float = 1.0,

    calendar: Optional[
        ql.Calendar
    ] = None,

    day_counter: Optional[
        ql.DayCounter
    ] = None,

) -> dict:

    """
    Price a callable hybrid capital security
    with periodic coupon payments.
    """

    calendar = (
        calendar
        or
        ql.TARGET()
    )

    day_counter = (
        day_counter
        or
        ql.Actual365Fixed()
    )

    valuation_date = _to_ql_date(
        valuation_date
    )

    issue_date = _to_ql_date(
        issue_date
    )

    maturity = _to_ql_date(
        maturity
    )

    first_call_contractual_date = _to_ql_date(
        first_call_date
    )

    first_call_date = calendar.adjust(
        first_call_contractual_date,
        ql.ModifiedFollowing
    )

    maturity_payment_date = calendar.adjust(
        maturity,
        ql.ModifiedFollowing
    )

    (
        x_paths,
        y_paths,
        short_rates,
        times,
        phi,
        simulation_today
    ) = _extract_short_rates(
        simulation
    )

    n_paths = (
        x_paths.shape[0]
    )

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    # ========================================================
    # Validation
    # ========================================================

    if valuation_date != simulation_today:

        raise ValueError(
            "valuation_date와 simulation['today']가 다릅니다. "
            f"valuation_date={valuation_date}, "
            f"simulation_today={simulation_today}"
        )

    if not (
        issue_date
        <
        maturity
    ):

        raise ValueError(
            "issue_date는 maturity보다 이전이어야 합니다."
        )

    if not (
        issue_date
        <
        first_call_contractual_date
        <
        maturity
    ):

        raise ValueError(
            "first_call_date는 issue_date 이후, "
            "maturity 이전이어야 합니다."
        )

    if maturity <= valuation_date:

        raise ValueError(
            "maturity는 valuation_date 이후여야 합니다."
        )

    if notional <= 0:

        raise ValueError(
            "notional은 0보다 커야 합니다."
        )

    if call_price_pct <= 0:

        raise ValueError(
            "call_price_pct는 0보다 커야 합니다."
        )

    mode = str(
        reset_rate_mode
    ).strip().upper()

    if mode not in {
        "FIXED",
        "YTM_PLUS_SPREAD"
    }:

        raise ValueError(
            "reset_rate_mode는 "
            "'FIXED' 또는 'YTM_PLUS_SPREAD' 여야 합니다."
        )

    if (
        mode == "FIXED"
        and
        reset_fixed_rate is None
    ):

        raise ValueError(
            "FIXED reset 방식은 "
            "reset_fixed_rate가 필요합니다."
        )

    # ========================================================
    # Pathwise discount factors
    # ========================================================

    cumulative_df = (
        _build_cumulative_discount_factors(
            short_rates,
            times
        )
    )

    # ========================================================
    # Coupon schedule
    # ========================================================

    coupon_periods = _coupon_periods(
        issue_date,
        maturity,
        coupon_frequency,
        calendar,
        day_counter
    )

    coupon_dates = [
        p[2]
        for p in coupon_periods
    ]

    # ========================================================
    # First call / reset alignment
    # ========================================================

    if first_call_date not in coupon_dates:

        raise ValueError(
            "first_call_date/reset date가 coupon schedule과 일치하지 않습니다. "
            f"입력 계약일={first_call_contractual_date}, "
            f"영업일 조정일={first_call_date}. "
            "Issue Date와 Coupon Frequency를 확인해 주세요."
        )

    # ========================================================
    # Active call dates
    # ========================================================

    all_call_dates = _generate_dates(
        first_call_contractual_date,
        maturity,
        call_frequency,
        calendar,
        include_start=True,
        include_end=False,
    )

    call_dates = [
        d
        for d in all_call_dates
        if valuation_date < d < maturity
    ]

    # ========================================================
    # Reset coupon
    # ========================================================

    reset_benchmark_ytm = None

    if mode == "FIXED":

        post_reset_coupon_rate = np.full(
            n_paths,
            float(
                reset_fixed_rate
            ),
            dtype=float
        )

    else:

        if first_call_date <= valuation_date:

            if reset_coupon_rate_override is None:

                raise ValueError(
                    "reset date가 valuation_date 이전/당일입니다. "
                    "과거에 확정된 reset coupon rate를 "
                    "reset_coupon_rate_override로 입력해 주세요."
                )

            post_reset_coupon_rate = np.full(
                n_paths,
                float(
                    reset_coupon_rate_override
                ),
                dtype=float
            )

        else:

            reset_index = (
                _date_to_simulation_index(
                    first_call_date,
                    valuation_date,
                    times,
                    day_counter
                )
            )

            reset_benchmark_ytm = (
                _benchmark_par_ytm_at_reset(
                    cumulative_df=cumulative_df,
                    reset_index=reset_index,
                    reset_date=first_call_date,
                    benchmark_tenor=benchmark_tenor,
                    benchmark_frequency=benchmark_frequency,
                    valuation_date=valuation_date,
                    times=times,
                    calendar=calendar,
                    day_counter=day_counter,
                )
            )

            post_reset_coupon_rate = (
                reset_benchmark_ytm
                +
                float(
                    reset_spread
                )
            )

    # ========================================================
    # Future coupon cash flows
    # ========================================================

    future_coupon_records = []

    for (
        start,
        end,
        pay,
        accrual
    ) in coupon_periods:

        if pay <= valuation_date:

            continue

        idx = (
            _date_to_simulation_index(
                pay,
                valuation_date,
                times,
                day_counter
            )
        )

        if idx >= len(times):

            raise ValueError(
                "Coupon date가 simulation horizon을 "
                f"초과합니다: {pay}"
            )

        if pay <= first_call_date:

            rate_vec = np.full(
                n_paths,
                float(
                    initial_coupon_rate
                ),
                dtype=float
            )

            rule = (
                "INITIAL_FIXED"
            )

        else:

            rate_vec = (
                post_reset_coupon_rate
            )

            rule = mode

        amount_vec = (
            notional
            *
            accrual
            *
            rate_vec
        )

        future_coupon_records.append(
            {
                "start": start,
                "end": end,
                "pay": pay,
                "index": idx,
                "accrual": accrual,
                "rate": rate_vec,
                "amount": amount_vec,
                "rule": rule,
            }
        )

    # ========================================================
    # Maturity
    # ========================================================

    maturity_index = (
        _date_to_simulation_index(
            maturity_payment_date,
            valuation_date,
            times,
            day_counter
        )
    )

    if maturity_index >= len(times):

        raise ValueError(
            "Maturity가 simulation horizon을 초과합니다."
        )

    # ========================================================
    # Straight bond value
    # ========================================================

    straight_pv = (
        notional
        *
        cumulative_df[
            :,
            maturity_index
        ]
        /
        cumulative_df[
            :,
            0
        ]
    )

    for rec in future_coupon_records:

        straight_pv += (
            rec["amount"]
            *
            cumulative_df[
                :,
                rec["index"]
            ]
            /
            cumulative_df[
                :,
                0
            ]
        )

    straight_price = float(
        np.mean(
            straight_pv
        )
    )

    # ========================================================
    # No remaining future call dates
    # ========================================================

    if len(call_dates) == 0:

        price = (
            straight_price
        )

        if n_paths > 1:

            se = float(
                np.std(
                    straight_pv,
                    ddof=1
                )
                /
                math.sqrt(
                    n_paths
                )
            )

        else:

            se = 0.0

        return {

            "Valuation Date":
                valuation_date,

            "Issue Date":
                issue_date,

            "Maturity":
                maturity,

            "First Call / Reset Date":
                first_call_contractual_date,

            "Adjusted First Call / Reset Date":
                first_call_date,

            "Coupon Frequency":
                str(
                    coupon_frequency
                ),

            "Call Frequency":
                str(
                    call_frequency
                ),

            "Initial Coupon Rate":
                initial_coupon_rate,

            "Reset Rate Mode":
                mode,

            "Reset Fixed Rate":
                reset_fixed_rate,

            "Benchmark Tenor":
                str(
                    benchmark_tenor
                ),

            "Reset Spread":
                reset_spread,

            "Reset Benchmark YTM Paths":
                reset_benchmark_ytm,

            "Post Reset Coupon Rate Paths":
                post_reset_coupon_rate,

            "Notional":
                notional,

            "Call Price %":
                call_price_pct,

            "Straight Bond Price":
                straight_price,

            "Callable Bond Price":
                price,

            "Callable Option Value":
                0.0,

            "Standard Error":
                se,

            "95% CI Lower":
                price - 1.96 * se,

            "95% CI Upper":
                price + 1.96 * se,

            "Exercise Statistics":
                pd.DataFrame(),

            "Path Values":
                straight_pv,
        }

    # ========================================================
    # Call indices
    # ========================================================

    call_indices = np.array(
        [
            _date_to_simulation_index(
                d,
                valuation_date,
                times,
                day_counter
            )
            for d in call_dates
        ],
        dtype=int
    )

    if np.any(
        call_indices
        >=
        len(times)
    ):

        raise ValueError(
            "Call date가 simulation horizon을 초과합니다."
        )

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

    continuation_estimates = np.zeros(
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

    redemption = (
        notional
        *
        call_price_pct
    )

    # ========================================================
    # Backward induction
    # ========================================================

    for k in range(
        n_calls - 1,
        -1,
        -1
    ):

        current_date = (
            call_dates[k]
        )

        current_idx = (
            call_indices[k]
        )

        base_df = cumulative_df[
            :,
            current_idx
        ]

        # ----------------------------------------------------
        # Last call date
        # ----------------------------------------------------

        if k == n_calls - 1:

            actual_continuation = (
                notional
                *
                cumulative_df[
                    :,
                    maturity_index
                ]
                /
                base_df
            )

            for rec in future_coupon_records:

                if rec["pay"] > current_date:

                    actual_continuation += (
                        rec["amount"]
                        *
                        cumulative_df[
                            :,
                            rec["index"]
                        ]
                        /
                        base_df
                    )

        # ----------------------------------------------------
        # Earlier call dates
        # ----------------------------------------------------

        else:

            next_date = (
                call_dates[
                    k + 1
                ]
            )

            next_idx = (
                call_indices[
                    k + 1
                ]
            )

            actual_continuation = (
                callable_values[
                    :,
                    k + 1
                ]
                *
                cumulative_df[
                    :,
                    next_idx
                ]
                /
                base_df
            )

            # Coupon on next call date is paid
            # before next exercise decision.

            for rec in future_coupon_records:

                if (
                    current_date
                    <
                    rec["pay"]
                    <=
                    next_date
                ):

                    actual_continuation += (
                        rec["amount"]
                        *
                        cumulative_df[
                            :,
                            rec["index"]
                        ]
                        /
                        base_df
                    )

        # ----------------------------------------------------
        # LSM regression
        # ----------------------------------------------------

        continuation_estimate = (
            _lsm_regression(
                x_paths[
                    :,
                    current_idx
                ],
                y_paths[
                    :,
                    current_idx
                ],
                actual_continuation,
            )
        )

        continuation_estimates[
            :,
            k
        ] = continuation_estimate

        # ----------------------------------------------------
        # Issuer call decision
        # ----------------------------------------------------

        exercise_now = (
            redemption
            <
            continuation_estimate
        )

        callable_values[
            :,
            k
        ] = np.where(
            exercise_now,
            redemption,
            actual_continuation
        )

        exercise_date_index[
            exercise_now
        ] = k

    # ========================================================
    # Valuation date -> first active call date
    # ========================================================

    first_call_date_active = (
        call_dates[0]
    )

    first_call_idx = (
        call_indices[0]
    )

    pv_paths = (
        callable_values[
            :,
            0
        ]
        *
        cumulative_df[
            :,
            first_call_idx
        ]
        /
        cumulative_df[
            :,
            0
        ]
    )

    # Coupon is paid before the call decision.
    for rec in future_coupon_records:

        if (
            rec["pay"]
            <=
            first_call_date_active
        ):

            pv_paths += (
                rec["amount"]
                *
                cumulative_df[
                    :,
                    rec["index"]
                ]
                /
                cumulative_df[
                    :,
                    0
                ]
            )

    # ========================================================
    # Callable price
    # ========================================================

    price = float(
        np.mean(
            pv_paths
        )
    )

    if n_paths > 1:

        se = float(
            np.std(
                pv_paths,
                ddof=1
            )
            /
            math.sqrt(
                n_paths
            )
        )

    else:

        se = 0.0

    # ========================================================
    # Exercise statistics
    # ========================================================

    exercise_rows = []

    for (
        k,
        d
    ) in enumerate(
        call_dates
    ):

        count = int(
            np.sum(
                exercise_date_index
                ==
                k
            )
        )

        exercise_rows.append(
            {
                "Call Date":
                    d,

                "Call Price":
                    redemption,

                "Exercise Paths":
                    count,

                "Exercise %":
                    count
                    /
                    n_paths
                    *
                    100.0,
            }
        )

    exercise_df = pd.DataFrame(
        exercise_rows
    )

    # ========================================================
    # Coupon schedule summary
    # ========================================================

    coupon_summary = []

    for rec in future_coupon_records:

        r = rec[
            "rate"
        ]

        coupon_summary.append(
            {
                "Accrual Start":
                    rec["start"],

                "Accrual End":
                    rec["end"],

                "Payment Date":
                    rec["pay"],

                "Accrual Factor":
                    rec["accrual"],

                "Coupon Rule":
                    rec["rule"],

                "Mean Coupon Rate":
                    float(
                        np.mean(
                            r
                        )
                    ),

                "Min Coupon Rate":
                    float(
                        np.min(
                            r
                        )
                    ),

                "Max Coupon Rate":
                    float(
                        np.max(
                            r
                        )
                    ),

                "Mean Coupon Amount":
                    float(
                        np.mean(
                            rec["amount"]
                        )
                    ),
            }
        )

    coupon_df = pd.DataFrame(
        coupon_summary
    )

    # ========================================================
    # Result
    # ========================================================

    return {

        "Valuation Date":
            valuation_date,

        "Issue Date":
            issue_date,

        "Maturity":
            maturity,

        "First Call / Reset Date":
            first_call_contractual_date,

        "Adjusted First Call / Reset Date":
            first_call_date,

        "Coupon Frequency":
            str(
                coupon_frequency
            ),

        "Call Frequency":
            str(
                call_frequency
            ),

        "Initial Coupon Rate":
            initial_coupon_rate,

        "Reset Rate Mode":
            mode,

        "Reset Fixed Rate":
            reset_fixed_rate,

        "Benchmark Tenor":
            str(
                benchmark_tenor
            ),

        "Benchmark Frequency":
            str(
                benchmark_frequency
            ),

        "Reset Spread":
            reset_spread,

        "Reset Benchmark YTM Paths":
            reset_benchmark_ytm,

        "Post Reset Coupon Rate Paths":
            post_reset_coupon_rate,

        "Mean Post Reset Coupon Rate":
            float(
                np.mean(
                    post_reset_coupon_rate
                )
            ),

        "Notional":
            notional,

        "Call Price %":
            call_price_pct,

        "Call Dates":
            call_dates,

        "Coupon Schedule":
            coupon_df,

        "Straight Bond Price":
            straight_price,

        "Callable Bond Price":
            price,

        "Callable Option Value":
            straight_price
            -
            price,

        "Standard Error":
            se,

        "95% CI Lower":
            price
            -
            1.96
            *
            se,

        "95% CI Upper":
            price
            +
            1.96
            *
            se,

        "Exercise Statistics":
            exercise_df,

        "Path Values":
            pv_paths,

        "Exercise Date Index":
            exercise_date_index,

        "Callable Values":
            callable_values,

        "Continuation Estimates":
            continuation_estimates,

        "X Paths":
            x_paths,

        "Y Paths":
            y_paths,

        "Phi Path":
            phi,

        "Short Rate Paths":
            short_rates,
    }


# ============================================================
# Excel input reader / batch pricer
# ============================================================

_EXCEL_DATE_FIELDS = {
    "Valuation Date",
    "Issue Date",
    "Maturity",
    "First Call / Reset Date"
}


def read_callable_hybrid_bond_excel(
    file_path: str,
    sheet_name: str = "Valuation"
) -> List[dict]:

    """
    Read the column-oriented batch input workbook.

    Supported header layouts
    ------------------------
    Legacy:
        A3 = "Parameter"

    Current workbook:
        A3 = "Field"
        B3:I3 = Bond 1 ... Bond 8
        J3 = Description
        K3 = Source

    Only columns whose header starts with "Bond" are treated as
    valuation records. Description / Source columns are ignored.
    """

    df = pd.read_excel(
        file_path,
        sheet_name=sheet_name,
        header=None
    )

    if df.shape[1] < 2:
        raise ValueError(
            "Valuation sheet에 최소 1개 Bond 입력 열이 필요합니다."
        )

    # ========================================================
    # 1. Locate header row
    # ========================================================

    parameter_row = None
    accepted_headers = {
        "PARAMETER",
        "FIELD",
    }

    for i in range(
        min(
            len(df),
            30
        )
    ):

        first_cell = str(
            df.iloc[
                i,
                0
            ]
        ).strip().upper()

        if first_cell in accepted_headers:

            parameter_row = i
            break

    if parameter_row is None:

        raise ValueError(
            "Valuation sheet에서 'Field' 또는 'Parameter' 행을 "
            "찾을 수 없습니다."
        )

    # ========================================================
    # 2. Identify Bond columns
    #
    # Current workbook:
    #   B:I = Bond 1 ... Bond 8
    #   J   = Description
    #   K   = Source
    #
    # Therefore only header cells beginning with "Bond" are read.
    # ========================================================

    bond_columns = []

    for col in range(
        1,
        df.shape[1]
    ):

        header_value = df.iloc[
            parameter_row,
            col
        ]

        if pd.isna(
            header_value
        ):
            continue

        header_text = str(
            header_value
        ).strip()

        if not header_text:
            continue

        if header_text.upper().startswith(
            "BOND"
        ):

            bond_columns.append(
                col
            )

    if not bond_columns:

        raise ValueError(
            "Valuation sheet에서 Bond 입력 열을 찾을 수 없습니다. "
            "Header는 'Bond 1', 'Bond 2'와 같은 형식이어야 합니다."
        )

    # ========================================================
    # 3. Parameter labels
    # ========================================================

    labels = df.iloc[
        parameter_row + 1:,
        0
    ].tolist()

    records = []

    # ========================================================
    # 4. Read each Bond
    # ========================================================

    for col in bond_columns:

        column_header = df.iloc[
            parameter_row,
            col
        ]

        rec = {
            "Bond Name":
                str(
                    column_header
                ).strip()
        }

        for (
            r_offset,
            label
        ) in enumerate(
            labels,
            start=parameter_row + 1
        ):

            if pd.isna(
                label
            ):
                continue

            key = str(
                label
            ).strip()

            if not key:
                continue

            value = df.iloc[
                r_offset,
                col
            ]

            if pd.isna(
                value
            ):
                value = None

            if (
                key in _EXCEL_DATE_FIELDS
                and
                value is not None
            ):

                value = _to_ql_date(
                    pd.to_datetime(
                        value
                    )
                )

            rec[
                key
            ] = value

        # ----------------------------------------------------
        # Bond Name:
        # If a "Bond Name" field exists in the rows, it
        # overwrites the generic column header "Bond 1", etc.
        # If blank, retain column header.
        # ----------------------------------------------------

        bond_name = rec.get(
            "Bond Name"
        )

        if (
            bond_name is None
            or
            str(
                bond_name
            ).strip() == ""
        ):

            rec[
                "Bond Name"
            ] = str(
                column_header
            ).strip()

        else:

            rec[
                "Bond Name"
            ] = str(
                bond_name
            ).strip()

        # ====================================================
        # Evaluate? normalization
        # ====================================================

        eval_flag = rec.get(
            "Evaluate?",
            True
        )

        if eval_flag is None:

            eval_flag = False

        elif isinstance(
            eval_flag,
            str
        ):

            eval_flag = (
                eval_flag
                .strip()
                .upper()
                in
                {
                    "TRUE",
                    "Y",
                    "YES",
                    "1",
                    "ON",
                    "CHECKED",
                }
            )

        elif isinstance(
            eval_flag,
            (int, float, np.integer, np.floating)
        ):

            eval_flag = bool(
                eval_flag
            )

        else:

            eval_flag = bool(
                eval_flag
            )

        rec[
            "Evaluate?"
        ] = eval_flag

        records.append(
            rec
        )

    return records

def price_callable_hybrid_bond_batch(

    simulation: dict,

    excel_path: str,

    sheet_name: str = "Valuation",

) -> Dict[
    str,
    dict
]:

    inputs = (
        read_callable_hybrid_bond_excel(
            excel_path,
            sheet_name=sheet_name
        )
    )

    results = {}

    for rec in inputs:

        if not rec.get(
            "Evaluate?",
            True
        ):

            continue

        name = (
            rec["Bond Name"]
        )

        mode = str(
            rec.get(
                "Reset Rate Mode",
                "FIXED"
            )
        ).strip().upper()

        reset_fixed = (
            rec.get(
                "Reset Fixed Rate"
            )
        )

        override = (
            rec.get(
                "Reset Coupon Rate Override"
            )
        )

        results[
            name
        ] = price_callable_hybrid_bond(

            simulation=simulation,

            valuation_date=
                rec[
                    "Valuation Date"
                ],

            issue_date=
                rec[
                    "Issue Date"
                ],

            maturity=
                rec[
                    "Maturity"
                ],

            first_call_date=
                rec[
                    "First Call / Reset Date"
                ],

            initial_coupon_rate=
                float(
                    rec[
                        "Initial Coupon Rate"
                    ]
                ),

            coupon_frequency=
                str(
                    rec.get(
                        "Coupon Frequency",
                        "3M"
                    )
                ),

            call_frequency=
                str(
                    rec.get(
                        "Call Frequency",
                        "3M"
                    )
                ),

            reset_rate_mode=
                mode,

            reset_fixed_rate=
                (
                    None
                    if reset_fixed is None
                    else float(
                        reset_fixed
                    )
                ),

            benchmark_tenor=
                str(
                    rec.get(
                        "Benchmark Tenor",
                        "5Y"
                    )
                ),

            benchmark_frequency=
                str(
                    rec.get(
                        "Benchmark Frequency",
                        "6M"
                    )
                ),

            reset_spread=
                float(
                    rec.get(
                        "Reset Spread",
                        0.0
                    )
                    or
                    0.0
                ),

            reset_coupon_rate_override=
                (
                    None
                    if override is None
                    else float(
                        override
                    )
                ),

            notional=
                float(
                    rec.get(
                        "Notional",
                        10000.0
                    )
                ),

            call_price_pct=
                float(
                    rec.get(
                        "Call Price %",
                        1.0
                    )
                ),
        )

    return results


# ============================================================
# Result printer
# ============================================================

def print_callable_hybrid_bond_result(

    result: dict,

    show_coupon_schedule: bool = True

) -> None:

    print()

    print(
        "=" * 88
    )

    print(
        "Callable Hybrid Bond / Hybrid Capital Security"
    )

    print(
        "=" * 88
    )

    print(
        f"Valuation Date             : "
        f"{result['Valuation Date']}"
    )

    print(
        f"Issue Date                 : "
        f"{result['Issue Date']}"
    )

    print(
        f"Maturity                   : "
        f"{result['Maturity']}"
    )

    print(
        f"First Call / Reset Date    : "
        f"{result['First Call / Reset Date']}"
    )

    print(
        f"Coupon Frequency           : "
        f"{result['Coupon Frequency']}"
    )

    print(
        f"Call Frequency             : "
        f"{result['Call Frequency']}"
    )

    print(
        f"Initial Coupon Rate        : "
        f"{result['Initial Coupon Rate']:.6%}"
    )

    print(
        f"Reset Rate Mode            : "
        f"{result['Reset Rate Mode']}"
    )

    if result.get(
        "Reset Fixed Rate"
    ) is not None:

        print(
            f"Reset Fixed Rate           : "
            f"{result['Reset Fixed Rate']:.6%}"
        )

    if (
        result.get(
            "Reset Rate Mode"
        )
        ==
        "YTM_PLUS_SPREAD"
    ):

        print(
            f"Benchmark Tenor            : "
            f"{result.get('Benchmark Tenor')}"
        )

        print(
            f"Reset Spread               : "
            f"{result.get('Reset Spread', 0.0):.6%}"
        )

    if result.get(
        "Mean Post Reset Coupon Rate"
    ) is not None:

        print(
            f"Mean Post-Reset Coupon Rate: "
            f"{result['Mean Post Reset Coupon Rate']:.6%}"
        )

    print(
        f"Notional                   : "
        f"{result['Notional']:,.2f}"
    )

    print(
        f"Call Price %               : "
        f"{result['Call Price %']:.6f}"
    )

    print()

    print(
        f"Straight Bond Price        : "
        f"{result['Straight Bond Price']:,.4f}"
    )

    print(
        f"Callable Bond Price        : "
        f"{result['Callable Bond Price']:,.4f}"
    )

    print(
        f"Callable Option Value      : "
        f"{result['Callable Option Value']:,.4f}"
    )

    print(
        f"Standard Error             : "
        f"{result['Standard Error']:,.6f}"
    )

    print(
        f"95% CI                     : "
        f"[{result['95% CI Lower']:,.4f}, "
        f"{result['95% CI Upper']:,.4f}]"
    )

    ex = result.get(
        "Exercise Statistics"
    )

    if (
        isinstance(
            ex,
            pd.DataFrame
        )
        and
        not ex.empty
    ):

        print()

        print(
            "Call / Exercise Statistics"
        )

        print(
            ex.to_string(
                index=False
            )
        )

    cs = result.get(
        "Coupon Schedule"
    )

    if (
        show_coupon_schedule
        and
        isinstance(
            cs,
            pd.DataFrame
        )
        and
        not cs.empty
    ):

        print()

        print(
            "Coupon Schedule"
        )

        print(
            cs.to_string(
                index=False
            )
        )