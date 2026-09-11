# ============================================================
# CallableSwap.py
#
# G2++ Monte Carlo
# + Bermudan Callable Swap
# + Longstaff-Schwartz
#
# FAST VERSION
#
# 주요 최적화
# ------------------------------------------------------------
# 1. Path별 cumulative integral을 한 번만 계산
# 2. Discount Factor를 반복 적분하지 않고
#       DF(i,j) = DF(0,j) / DF(0,i)
#    형태로 계산
# 3. Date -> Simulation Index 사전 계산
# 4. Swap Value 계산을 vectorization
# 5. Path별 Python loop 제거
# 6. LSM은 NumPy matrix 연산 사용
# 7. Zero Coupon Callable Bond도 동일한 방식으로 최적화
# ============================================================

import QuantLib as ql
import numpy as np
import pandas as pd


# ============================================================
# 1. Build Payment Schedule
# ============================================================

def _build_schedule(
    start_date,
    maturity_date,
    tenor
):

    calendar = ql.TARGET()

    schedule = ql.Schedule(
        start_date,
        maturity_date,
        ql.Period(tenor),
        calendar,
        ql.ModifiedFollowing,
        ql.ModifiedFollowing,
        ql.DateGeneration.Forward,
        False
    )

    return schedule


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

    return int(round(t / dt))


# ============================================================
# 3. G2++ Deterministic Shift phi(t)
# ============================================================

def _g2_phi(
    t,
    curve,
    a,
    sigma,
    b,
    eta,
    rho,
    valuation_date,
    day_counter
):

    if t <= 0.0:

        return curve.forwardRate(
            valuation_date,
            valuation_date + ql.Period(
                1,
                ql.Days
            ),
            day_counter,
            ql.Continuous
        ).rate()

    months = max(
        1,
        int(round(t * 12))
    )

    date_t = (
        valuation_date
        + ql.Period(
            months,
            ql.Months
        )
    )

    date_t1 = (
        date_t
        + ql.Period(
            1,
            ql.Months
        )
    )

    try:

        forward_rate = curve.forwardRate(
            date_t,
            date_t1,
            day_counter,
            ql.Continuous
        ).rate()

    except Exception:

        zero_rate = curve.zeroRate(
            date_t,
            day_counter,
            ql.Continuous
        ).rate()

        forward_rate = zero_rate

    exp_a = np.exp(
        -a * t
    )

    exp_b = np.exp(
        -b * t
    )

    term_x = (

        sigma ** 2
        / (2.0 * a ** 2)
        * (1.0 - exp_a) ** 2

    )

    term_y = (

        eta ** 2
        / (2.0 * b ** 2)
        * (1.0 - exp_b) ** 2

    )

    term_xy = (

        rho
        * sigma
        * eta
        / (a * b)
        * (1.0 - exp_a)
        * (1.0 - exp_b)

    )

    return (
        forward_rate
        + term_x
        + term_y
        + term_xy
    )


# ============================================================
# 4. Build phi Path
# ============================================================

def _build_phi_path(
    simulation,
    valuation_date
):

    times = np.asarray(
        simulation["times"]
    )

    curve = simulation["curve"]

    model = simulation["model"]

    day_counter = ql.Actual365Fixed()

    params = model.params()

    a = params[0]
    sigma = params[1]

    b = params[2]
    eta = params[3]

    rho = params[4]

    phi = np.zeros(
        len(times)
    )

    for i, t in enumerate(times):

        phi[i] = _g2_phi(

            t,
            curve,
            a,
            sigma,
            b,
            eta,
            rho,
            valuation_date,
            day_counter

        )

    return phi


# ============================================================
# 5. Build Short Rate
# ============================================================

def _build_short_rate_from_xy(
    x_path,
    y_path,
    phi
):

    return (
        x_path
        + y_path
        + phi
    )


# ============================================================
# 6. Build Cumulative Discount Factor
#
# 핵심 최적화 함수
#
# 각 path에 대해
#
#   I(t) = integral_0^t r(s) ds
#
# 를 한 번만 계산.
#
# 이후
#
#   P(i,j)
#   =
#   exp[-(I(j)-I(i))]
#
# ============================================================

def _build_cumulative_discount_factors(
    short_rates,
    dt
):

    short_rates = np.asarray(
        short_rates
    )

    if short_rates.ndim == 1:

        rates = short_rates

        n = len(rates)

        cumulative_integral = np.zeros(n)

        if n > 1:

            cumulative_integral[1:] = np.cumsum(

                0.5
                * (
                    rates[:-1]
                    + rates[1:]
                )
                * dt

            )

        return np.exp(
            -cumulative_integral
        )

    # --------------------------------------------------------
    # 2D
    #
    # shape = (n_paths, n_times)
    # --------------------------------------------------------

    n_paths, n_times = (
        short_rates.shape
    )

    cumulative_integral = np.zeros_like(
        short_rates
    )

    if n_times > 1:

        increments = (

            0.5
            * (
                short_rates[:, :-1]
                + short_rates[:, 1:]
            )
            * dt

        )

        cumulative_integral[:, 1:] = (
            np.cumsum(
                increments,
                axis=1
            )
        )

    return np.exp(
        -cumulative_integral
    )


# ============================================================
# 7. Path Discount Factor
#
# 기존 함수와 동일한 결과를 반환하지만
# 가능하면 cumulative DF를 사용
# ============================================================

def _path_discount_factor(
    short_rate_path,
    start_index,
    end_index,
    dt,
    cumulative_df=None
):

    if end_index <= start_index:

        return 1.0

    if cumulative_df is not None:

        return (

            cumulative_df[end_index]
            / cumulative_df[start_index]

        )

    rates = short_rate_path[
        start_index:
        end_index + 1
    ]

    integral = (

        0.5 * rates[0]
        + np.sum(rates[1:-1])
        + 0.5 * rates[-1]

    ) * dt

    return np.exp(
        -integral
    )


# ============================================================
# 8. Vectorized Discount Factor
#
# start_indices / end_indices
# 모두 배열
# ============================================================

def _vectorized_discount_factor(
    cumulative_df,
    start_indices,
    end_indices
):

    start_indices = np.asarray(
        start_indices,
        dtype=int
    )

    end_indices = np.asarray(
        end_indices,
        dtype=int
    )

    result = (

        cumulative_df[
            :,
            end_indices
        ]
        /
        cumulative_df[
            :,
            start_indices
        ]

    )

    result = np.where(
        end_indices > start_indices,
        result,
        1.0
    )

    return result


# ============================================================
# 9. Path Forward Rate
#
# P(t,T1) / P(t,T2)
# ============================================================

def _path_forward_rate(
    short_rate_path,
    valuation_index,
    start_index,
    end_index,
    dt,
    tau,
    cumulative_df=None
):

    if end_index <= start_index:

        return short_rate_path[
            valuation_index
        ]

    if cumulative_df is not None:

        p1 = (

            cumulative_df[end_index]
            * 0.0
            + cumulative_df[start_index]

        )

        p2 = cumulative_df[end_index]

        forward_rate = (

            p1 / p2
            - 1.0

        ) / tau

        return forward_rate

    p1 = _path_discount_factor(

        short_rate_path,
        valuation_index,
        start_index,
        dt

    )

    p2 = _path_discount_factor(

        short_rate_path,
        valuation_index,
        end_index,
        dt

    )

    if p2 <= 0.0:

        return 0.0

    return (
        p1 / p2 - 1.0
    ) / tau


# ============================================================
# 10. LSM Basis
# ============================================================

def _lsm_regression(
    x,
    y,
    continuation_value
):

    A = np.column_stack(
        [
            np.ones(len(x)),
            x,
            y,
            x ** 2,
            x * y,
            y ** 2
        ]
    )

    if len(x) < 6:

        return np.full(
            len(x),
            np.mean(continuation_value)
        )

    coefficients = np.linalg.lstsq(
        A,
        continuation_value,
        rcond=None
    )[0]

    return (
        A @ coefficients
    )


# ============================================================
# 11. Swap Value at Call Date
#
# 최적화 버전
#
# Path 하나에 대해 계산하되
# 각 cashflow의 discount / forward를
# cumulative DF에서 직접 계산
# ============================================================

def _swap_value_at_call_date_fast(

    cumulative_df,

    call_index,

    fixed_payment_indices,
    fixed_accruals,

    floating_start_indices,
    floating_payment_indices,
    floating_accruals,

    fixed_rate,
    floating_spread,

    receiver_payer,
    notional

):

    # ========================================================
    # Fixed Leg
    # ========================================================

    fixed_mask = (
        fixed_payment_indices
        > call_index
    )

    if np.any(fixed_mask):

        payment_indices = (
            fixed_payment_indices[
                fixed_mask
            ]
        )

        accruals = (
            fixed_accruals[
                fixed_mask
            ]
        )

        discount_factors = (

            cumulative_df[
                payment_indices
            ]
            /
            cumulative_df[
                call_index
            ]

        )

        fixed_pv = np.sum(

            fixed_rate
            * accruals
            * notional
            * discount_factors

        )

    else:

        fixed_pv = 0.0


    # ========================================================
    # Floating Leg
    # ========================================================

    floating_mask = (

        floating_payment_indices
        > call_index

    )

    if np.any(floating_mask):

        start_indices = (

            floating_start_indices[
                floating_mask
            ]

        )

        payment_indices = (

            floating_payment_indices[
                floating_mask
            ]

        )

        accruals = (

            floating_accruals[
                floating_mask
            ]

        )

        # ----------------------------------------------------
        # P(call, start)
        # P(call, payment)
        # ----------------------------------------------------

        p_start = (

            cumulative_df[
                start_indices
            ]
            /
            cumulative_df[
                call_index
            ]

        )

        p_payment = (

            cumulative_df[
                payment_indices
            ]
            /
            cumulative_df[
                call_index
            ]

        )

        # ----------------------------------------------------
        # Forward Rate
        #
        # [P(t,T1)/P(t,T2)-1] / tau
        # ----------------------------------------------------

        forward_rate = (

            p_start
            / p_payment
            - 1.0

        ) / accruals

        floating_rate = (

            forward_rate
            + floating_spread

        )

        floating_pv = np.sum(

            floating_rate
            * accruals
            * notional
            * p_payment

        )

    else:

        floating_pv = 0.0


    # ========================================================
    # Swap Value
    # ========================================================

    if receiver_payer.upper() == "RECEIVER":

        return (
            fixed_pv
            - floating_pv
        )

    elif receiver_payer.upper() == "PAYER":

        return (
            floating_pv
            - fixed_pv
        )

    else:

        raise ValueError(
            "receiver_payer는 "
            "'Receiver' 또는 'Payer'여야 합니다."
        )


# ============================================================
# 12. Callable Exercise Value
# ============================================================

def _call_exercise_value(
    swap_value,
    receiver_payer,
    call_right
):

    receiver_payer = (
        receiver_payer.upper()
    )

    call_right = (
        call_right.upper()
    )

    if call_right == "RECEIVER":

        if receiver_payer == "RECEIVER":

            return max(
                -swap_value,
                0.0
            )

        else:

            return max(
                swap_value,
                0.0
            )

    elif call_right == "PAYER":

        if receiver_payer == "PAYER":

            return max(
                -swap_value,
                0.0
            )

        else:

            return max(
                swap_value,
                0.0
            )

    else:

        raise ValueError(
            "call_right는 "
            "'Receiver' 또는 'Payer'여야 합니다."
        )


# ============================================================
# 13. Precompute Swap Schedule
#
# 날짜 관련 계산을 price 함수에서 한 번만 수행
# ============================================================

def _prepare_swap_cashflows(

    fixed_schedule,
    floating_schedule,

    trade_date,
    valuation_date,

    dt,
    day_counter

):

    # --------------------------------------------------------
    # Fixed
    # --------------------------------------------------------

    fixed_dates = list(
        fixed_schedule
    )

    fixed_payment_indices = []
    fixed_accruals = []

    for i in range(
        1,
        len(fixed_dates)
    ):

        payment_date = (
            fixed_dates[i]
        )

        if payment_date <= trade_date:
            continue

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
                fixed_dates[i - 1],
                payment_date
            )
        )

        fixed_payment_indices.append(
            payment_index
        )

        fixed_accruals.append(
            accrual
        )


    # --------------------------------------------------------
    # Floating
    # --------------------------------------------------------

    floating_dates = list(
        floating_schedule
    )

    floating_start_indices = []
    floating_payment_indices = []
    floating_accruals = []

    for i in range(
        1,
        len(floating_dates)
    ):

        payment_date = (
            floating_dates[i]
        )

        if payment_date <= trade_date:
            continue

        payment_index = (
            _date_to_simulation_index(
                payment_date,
                valuation_date,
                dt,
                day_counter
            )
        )

        start_index = (
            _date_to_simulation_index(
                floating_dates[i - 1],
                valuation_date,
                dt,
                day_counter
            )
        )

        accrual = (
            day_counter.yearFraction(
                floating_dates[i - 1],
                payment_date
            )
        )

        floating_start_indices.append(
            start_index
        )

        floating_payment_indices.append(
            payment_index
        )

        floating_accruals.append(
            accrual
        )


    return (

        np.asarray(
            fixed_payment_indices,
            dtype=int
        ),

        np.asarray(
            fixed_accruals,
            dtype=float
        ),

        np.asarray(
            floating_start_indices,
            dtype=int
        ),

        np.asarray(
            floating_payment_indices,
            dtype=int
        ),

        np.asarray(
            floating_accruals,
            dtype=float
        )

    )


# ============================================================
# 14. Bermudan Callable Swap Valuation
# ============================================================

def price_callable_swap(

    simulation,

    valuation_date,

    trade_date,

    maturity,

    call_dates,

    receiver_payer,

    call_right,

    fixed_rate,

    floating_spread=0.0,

    notional=1.0,

    fixed_leg_tenor="1Y",

    floating_leg_tenor="6M"

):

    # ========================================================
    # 1. Simulation Data
    # ========================================================

    x_paths = np.asarray(
        simulation["x"]
    )

    y_paths = np.asarray(
        simulation["y"]
    )

    times = np.asarray(
        simulation["times"]
    )

    simulation_today = (
        simulation["today"]
    )

    n_paths = x_paths.shape[0]

    if x_paths.shape != y_paths.shape:

        raise ValueError(
            "x와 y simulation path의 "
            "shape가 다릅니다."
        )

    if x_paths.shape[1] != len(times):

        raise ValueError(
            "x/y path의 시간 길이와 "
            "simulation['times'] 길이가 다릅니다."
        )

    if len(times) < 2:

        raise ValueError(
            "simulation['times']가 "
            "충분하지 않습니다."
        )

    dt = float(
        times[1] - times[0]
    )

    if dt <= 0.0:

        raise ValueError(
            "Simulation dt가 0 이하입니다."
        )


    # ========================================================
    # 2. Day Counter
    # ========================================================

    day_counter = ql.Actual365Fixed()


    # ========================================================
    # 3. Evaluation Date
    # ========================================================

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )


    # ========================================================
    # 4. Validation
    # ========================================================

    if valuation_date != simulation_today:

        raise ValueError(

            "valuation_date와 "
            "simulation['today']가 다릅니다.\n"

            f"valuation_date = {valuation_date}\n"
            f"simulation_today = {simulation_today}"

        )

    if trade_date > valuation_date:

        raise ValueError(
            "Callable Swap에서는 "
            "trade_date가 valuation_date "
            "이후일 수 없습니다."
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

    if len(call_dates) == 0:

        raise ValueError(
            "call_dates가 비어 있습니다."
        )


    # ========================================================
    # 5. Call Dates
    # ========================================================

    call_dates = sorted(
        call_dates
    )

    for date in call_dates:

        if date <= valuation_date:

            raise ValueError(

                f"Call Date {date}는 "
                "valuation_date 이후여야 합니다."

            )

        if date >= maturity:

            raise ValueError(

                f"Call Date {date}는 "
                "maturity보다 이전이어야 합니다."

            )


    # ========================================================
    # 6. Build Schedule
    # ========================================================

    fixed_schedule = _build_schedule(

        trade_date,
        maturity,
        fixed_leg_tenor

    )

    floating_schedule = _build_schedule(

        trade_date,
        maturity,
        floating_leg_tenor

    )


    # ========================================================
    # 7. Precompute Cashflows
    # ========================================================

    (
        fixed_payment_indices,
        fixed_accruals,
        floating_start_indices,
        floating_payment_indices,
        floating_accruals

    ) = _prepare_swap_cashflows(

        fixed_schedule,
        floating_schedule,

        trade_date,
        valuation_date,

        dt,
        day_counter

    )


    # ========================================================
    # 8. phi
    # ========================================================

    phi = _build_phi_path(

        simulation,
        valuation_date

    )


    # ========================================================
    # 9. Short Rates
    #
    # Vectorized
    # ========================================================

    short_rates = (

        x_paths
        + y_paths
        + phi[np.newaxis, :]

    )


    # ========================================================
    # 10. Cumulative Discount Factor
    #
    # ★ 가장 중요한 최적화
    # ========================================================

    cumulative_df = (
        _build_cumulative_discount_factors(
            short_rates,
            dt
        )
    )


    # ========================================================
    # 11. Call Indices
    # ========================================================

    call_indices = np.asarray(

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
        call_indices < 0
    ):

        raise ValueError(
            "Call Date index가 음수입니다."
        )

    if np.any(
        call_indices >= len(times)
    ):

        raise ValueError(
            "Call Date가 Simulation 기간을 "
            "초과합니다."
        )


    # ========================================================
    # 12. Exercise / Swap Value Matrix
    # ========================================================

    n_calls = len(
        call_dates
    )

    swap_values = np.zeros(

        (
            n_paths,
            n_calls
        )

    )

    exercise_values = np.zeros(

        (
            n_paths,
            n_calls
        )

    )


    # ========================================================
    # 13. Calculate Swap Value
    #
    # path loop는 남겨두지만
    # 각 path 내부 계산은 vectorized
    #
    # 기존:
    # path -> call -> fixed cashflow -> floating cashflow
    #
    # 현재:
    # path -> call
    #
    # 속도 개선
    # ========================================================

    for k, call_index in enumerate(
        call_indices
    ):

        # ----------------------------------------------------
        # Fixed Leg
        # ----------------------------------------------------

        fixed_mask = (

            fixed_payment_indices
            > call_index

        )

        if np.any(fixed_mask):

            payment_indices = (
                fixed_payment_indices[
                    fixed_mask
                ]
            )

            accruals = (
                fixed_accruals[
                    fixed_mask
                ]
            )

            discount_factors = (

                cumulative_df[
                    :,
                    payment_indices
                ]
                /
                cumulative_df[
                    :,
                    call_index
                ][:, np.newaxis]

            )

            fixed_pv = np.sum(

                fixed_rate
                * accruals[np.newaxis, :]
                * notional
                * discount_factors,

                axis=1

            )

        else:

            fixed_pv = np.zeros(
                n_paths
            )


        # ----------------------------------------------------
        # Floating Leg
        # ----------------------------------------------------

        floating_mask = (

            floating_payment_indices
            > call_index

        )

        if np.any(floating_mask):

            start_indices = (
                floating_start_indices[
                    floating_mask
                ]
            )

            payment_indices = (
                floating_payment_indices[
                    floating_mask
                ]
            )

            accruals = (
                floating_accruals[
                    floating_mask
                ]
            )

            p_start = (

                cumulative_df[
                    :,
                    start_indices
                ]
                /
                cumulative_df[
                    :,
                    call_index
                ][:, np.newaxis]

            )

            p_payment = (

                cumulative_df[
                    :,
                    payment_indices
                ]
                /
                cumulative_df[
                    :,
                    call_index
                ][:, np.newaxis]

            )

            forward_rate = (

                p_start
                / p_payment
                - 1.0

            ) / accruals[np.newaxis, :]

            floating_rate = (

                forward_rate
                + floating_spread

            )

            floating_pv = np.sum(

                floating_rate
                * accruals[np.newaxis, :]
                * notional
                * p_payment,

                axis=1

            )

        else:

            floating_pv = np.zeros(
                n_paths
            )


        # ----------------------------------------------------
        # Swap Value
        # ----------------------------------------------------

        if receiver_payer.upper() == "RECEIVER":

            values = (
                fixed_pv
                - floating_pv
            )

        elif receiver_payer.upper() == "PAYER":

            values = (
                floating_pv
                - fixed_pv
            )

        else:

            raise ValueError(
                "receiver_payer는 "
                "'Receiver' 또는 'Payer'여야 합니다."
            )


        swap_values[
            :,
            k
        ] = values


        # ----------------------------------------------------
        # Exercise Value
        # ----------------------------------------------------

        if call_right.upper() == "RECEIVER":

            if receiver_payer.upper() == "RECEIVER":

                exercise_values[
                    :,
                    k
                ] = np.maximum(
                    -values,
                    0.0
                )

            else:

                exercise_values[
                    :,
                    k
                ] = np.maximum(
                    values,
                    0.0
                )

        elif call_right.upper() == "PAYER":

            if receiver_payer.upper() == "PAYER":

                exercise_values[
                    :,
                    k
                ] = np.maximum(
                    -values,
                    0.0
                )

            else:

                exercise_values[
                    :,
                    k
                ] = np.maximum(
                    values,
                    0.0
                )

        else:

            raise ValueError(
                "call_right는 "
                "'Receiver' 또는 'Payer'여야 합니다."
            )


    # ========================================================
    # 14. LSM
    # ========================================================

    option_values = np.zeros(
        n_paths
    )

    exercised = np.zeros(
        n_paths,
        dtype=bool
    )

    exercise_date_index = np.full(
        n_paths,
        -1,
        dtype=int
    )


    # ========================================================
    # 15. Last Call
    # ========================================================

    last_call = (
        n_calls - 1
    )

    option_values[:] = (
        exercise_values[
            :,
            last_call
        ]
    )

    exercise_now = (

        exercise_values[
            :,
            last_call
        ]
        > 0.0

    )

    exercised[
        exercise_now
    ] = True

    exercise_date_index[
        exercise_now
    ] = last_call


    # ========================================================
    # 16. Backward Induction
    # ========================================================

    for k in range(

        last_call - 1,
        -1,
        -1

    ):

        alive = ~exercised

        if not np.any(alive):

            break

        call_index = (
            call_indices[k]
        )

        next_index = (
            call_indices[k + 1]
        )


        # ----------------------------------------------------
        # Continuation Discount
        #
        # cumulative DF 이용
        # ----------------------------------------------------

        continuation_discount = (

            cumulative_df[
                alive,
                next_index
            ]
            /
            cumulative_df[
                alive,
                call_index
            ]

        )


        continuation_value = (

            option_values[alive]
            * continuation_discount

        )


        # ----------------------------------------------------
        # State Variables
        # ----------------------------------------------------

        x = x_paths[
            alive,
            call_index
        ]

        y = y_paths[
            alive,
            call_index
        ]

        exercise_value = (

            exercise_values[
                alive,
                k
            ]

        )


        # ----------------------------------------------------
        # LSM
        # ----------------------------------------------------

        continuation_estimate = (
            _lsm_regression(
                x,
                y,
                continuation_value
            )
        )


        # ----------------------------------------------------
        # Exercise
        # ----------------------------------------------------

        exercise_now = (

            exercise_value
            > continuation_estimate

        )


        alive_indices = np.where(
            alive
        )[0]

        selected_paths = (
            alive_indices[
                exercise_now
            ]
        )


        # ----------------------------------------------------
        # Store
        # ----------------------------------------------------

        option_values[
            selected_paths
        ] = exercise_values[
            selected_paths,
            k
        ]

        exercised[
            selected_paths
        ] = True

        exercise_date_index[
            selected_paths
        ] = k


    # ========================================================
    # 17. Discount Exercise Value to Valuation Date
    # ========================================================

    present_values = np.zeros(
        n_paths
    )

    exercised_mask = (
        exercise_date_index >= 0
    )

    if np.any(exercised_mask):

        selected_k = (
            exercise_date_index[
                exercised_mask
            ]
        )

        selected_indices = (
            call_indices[
                selected_k
            ]
        )

        discount_factors = (

            cumulative_df[
                exercised_mask,
                selected_indices
            ]
            /
            cumulative_df[
                exercised_mask,
                0
            ]

        )

        present_values[
            exercised_mask
        ] = (

            option_values[
                exercised_mask
            ]
            * discount_factors

        )


    # ========================================================
    # 18. Monte Carlo Price
    # ========================================================

    price = np.mean(
        present_values
    )

    if n_paths > 1:

        standard_error = (

            np.std(
                present_values,
                ddof=1
            )
            / np.sqrt(n_paths)

        )

    else:

        standard_error = 0.0


    # ========================================================
    # 19. Exercise Statistics
    # ========================================================

    exercise_statistics = []

    for k, date in enumerate(
        call_dates
    ):

        count = np.sum(

            exercise_date_index
            == k

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
                int(count),

            "Exercise %":
                percentage

        })


    exercise_df = pd.DataFrame(
        exercise_statistics
    )


    # ========================================================
    # 20. Result
    # ========================================================

    result = {

        "Valuation Date":
            valuation_date,

        "Trade Date":
            trade_date,

        "Maturity":
            maturity,

        "Call Dates":
            call_dates,

        "Price":
            price,

        "Standard Error":
            standard_error,

        "95% CI Lower":
            price
            - 1.96 * standard_error,

        "95% CI Upper":
            price
            + 1.96 * standard_error,

        "Exercise Statistics":
            exercise_df,

        "Path Values":
            present_values,

        "Exercise Date Index":
            exercise_date_index,

        "Swap Values":
            swap_values,

        "Exercise Values":
            exercise_values,

        "X Paths":
            x_paths,

        "Y Paths":
            y_paths,

        "Phi Path":
            phi,

        "Short Rate Paths":
            short_rates,

        "Fixed Leg Tenor":
            fixed_leg_tenor,

        "Floating Leg Tenor":
            floating_leg_tenor,

        "Fixed Rate":
            fixed_rate,

        "Floating Spread":
            floating_spread,

        "Receiver / Payer":
            receiver_payer,

        "Call Right":
            call_right,

        "Notional":
            notional

    }

    return result


# ============================================================
# 21. Print Callable Swap Result
# ============================================================

def print_callable_swap_result(
    result
):

    print()

    print("=" * 75)

    print(
        "Bermudan Callable Swap"
    )

    print("=" * 75)

    print()

    print(
        f"Valuation Date     : "
        f"{result['Valuation Date']}"
    )

    print(
        f"Trade Date         : "
        f"{result['Trade Date']}"
    )

    print(
        f"Maturity           : "
        f"{result['Maturity']}"
    )

    print(
        f"Price              : "
        f"{result['Price']:,.2f}"
    )

    print(
        f"Standard Error     : "
        f"{result['Standard Error']:,.2f}"
    )

    print(
        f"95% CI             : "
        f"[{result['95% CI Lower']:,.2f}, "
        f"{result['95% CI Upper']:,.2f}]"
    )

    print()

    print(
        f"Receiver / Payer   : "
        f"{result['Receiver / Payer']}"
    )

    print(
        f"Call Right         : "
        f"{result['Call Right']}"
    )

    print(
        f"Fixed Rate         : "
        f"{result['Fixed Rate']:.6%}"
    )

    print(
        f"Floating Spread    : "
        f"{result['Floating Spread']:.6%}"
    )

    print(
        f"Fixed Leg Tenor    : "
        f"{result['Fixed Leg Tenor']}"
    )

    print(
        f"Floating Leg Tenor : "
        f"{result['Floating Leg Tenor']}"
    )

    print(
        f"Notional           : "
        f"{result['Notional']:,.0f}"
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


# ============================================================
# ============================================================
# Zero Coupon Callable Bond
# ============================================================
# ============================================================


# ============================================================
# 22. Zero Coupon Accrued Value
# ============================================================

def _zero_coupon_accrued_value(
    date,
    trade_date,
    zero_coupon_rate,
    notional,
    day_counter
):

    if date <= trade_date:

        return notional

    t = day_counter.yearFraction(
        trade_date,
        date
    )

    return (

        notional
        * (1.0 + zero_coupon_rate) ** t

    )


# ============================================================
# 23. Zero Coupon Maturity Value
# ============================================================

def _zero_coupon_maturity_value(
    trade_date,
    maturity,
    zero_coupon_rate,
    notional,
    day_counter
):

    t = day_counter.yearFraction(
        trade_date,
        maturity
    )

    return (

        notional
        * (1.0 + zero_coupon_rate) ** t

    )


# ============================================================
# 24. LSM Regression
# ============================================================

def _callable_bond_lsm_regression(
    x,
    y,
    continuation_value
):

    A = np.column_stack(

        [
            np.ones(len(x)),
            x,
            y,
            x ** 2,
            x * y,
            y ** 2
        ]

    )

    if len(x) < 6:

        return np.full(

            len(x),

            np.mean(
                continuation_value
            )

        )

    coefficients = np.linalg.lstsq(

        A,
        continuation_value,
        rcond=None

    )[0]

    return (
        A @ coefficients
    )


# ============================================================
# 25. Price Zero Coupon Callable Bond
#
# FAST VERSION
# ============================================================

def price_zero_coupon_callable_bond(

    simulation,

    valuation_date,

    trade_date,

    maturity,

    call_dates,

    zero_coupon_rate,

    notional=1.0

):

    # ========================================================
    # 1. Simulation
    # ========================================================

    x_paths = np.asarray(
        simulation["x"]
    )

    y_paths = np.asarray(
        simulation["y"]
    )

    times = np.asarray(
        simulation["times"]
    )

    simulation_today = (
        simulation["today"]
    )

    n_paths = x_paths.shape[0]

    if x_paths.shape != y_paths.shape:

        raise ValueError(
            "x와 y simulation path의 "
            "shape가 다릅니다."
        )

    if len(times) < 2:

        raise ValueError(
            "simulation['times']가 "
            "충분하지 않습니다."
        )

    if x_paths.shape[1] != len(times):

        raise ValueError(
            "x/y path의 시간 길이와 "
            "simulation['times'] 길이가 다릅니다."
        )

    dt = float(
        times[1] - times[0]
    )

    if dt <= 0.0:

        raise ValueError(
            "Simulation dt가 0 이하입니다."
        )


    # ========================================================
    # 2. Day Counter
    # ========================================================

    day_counter = ql.Actual365Fixed()


    # ========================================================
    # 3. Evaluation Date
    # ========================================================

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )


    # ========================================================
    # 4. Validation
    # ========================================================

    if valuation_date != simulation_today:

        raise ValueError(

            "valuation_date와 "
            "simulation['today']가 다릅니다.\n\n"

            f"valuation_date = {valuation_date}\n"
            f"simulation_today = {simulation_today}"

        )

    if trade_date > valuation_date:

        raise ValueError(

            "trade_date는 valuation_date보다 "
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

    if call_dates is None:

        raise ValueError(
            "call_dates가 None입니다."
        )

    if len(call_dates) == 0:

        raise ValueError(
            "call_dates가 비어 있습니다."
        )

    if zero_coupon_rate <= -1.0:

        raise ValueError(
            "zero_coupon_rate는 "
            "-100%보다 커야 합니다."
        )

    if notional <= 0.0:

        raise ValueError(
            "notional은 0보다 커야 합니다."
        )


    # ========================================================
    # 5. Active Call Dates
    # ========================================================

    call_dates = sorted(
        call_dates
    )

    active_call_dates = []

    for date in call_dates:

        if date >= maturity:

            raise ValueError(

                f"Call Date {date}는 "
                "maturity보다 이전이어야 합니다."

            )

        if date <= valuation_date:

            continue

        active_call_dates.append(
            date
        )

    call_dates = active_call_dates

    if len(call_dates) == 0:

        raise ValueError(

            "valuation_date 이후에 남아 있는 "
            "Call Date가 없습니다."

        )


    # ========================================================
    # 6. Short Rate
    # ========================================================

    if "short_rate" in simulation:

        short_rates = np.asarray(
            simulation["short_rate"]
        )

        if short_rates.shape != x_paths.shape:

            raise ValueError(
                "short_rate와 x/y path의 "
                "shape가 일치하지 않습니다."
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
            simulation["phi"]
        ).reshape(-1)

        if len(phi) != x_paths.shape[1]:

            raise ValueError(
                "simulation['phi'] 길이가 "
                "x/y path와 일치하지 않습니다."
            )

        short_rates = (

            x_paths
            + y_paths
            + phi[np.newaxis, :]

        )

    else:

        raise ValueError(

            "simulation에 "
            "'short_rate' 또는 'phi'가 필요합니다."

        )


    # ========================================================
    # 7. Cumulative DF
    # ========================================================

    cumulative_df = (

        _build_cumulative_discount_factors(

            short_rates,
            dt

        )

    )


    # ========================================================
    # 8. Call Indices
    # ========================================================

    call_indices = np.asarray(

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


    # ========================================================
    # 9. Maturity Index
    # ========================================================

    maturity_index = (

        _date_to_simulation_index(

            maturity,
            valuation_date,
            dt,
            day_counter

        )

    )

    if maturity_index >= len(times):

        raise ValueError(

            "Maturity가 Simulation 기간을 "
            "초과합니다."

        )


    # ========================================================
    # 10. Maturity Value
    # ========================================================

    maturity_value = (

        _zero_coupon_maturity_value(

            trade_date,
            maturity,
            zero_coupon_rate,
            notional,
            day_counter

        )

    )


    # ========================================================
    # 11. Call Prices
    # ========================================================

    call_prices = np.asarray(

        [

            _zero_coupon_accrued_value(

                date,
                trade_date,
                zero_coupon_rate,
                notional,
                day_counter

            )

            for date in call_dates

        ],

        dtype=float

    )


    # ========================================================
    # 12. Straight Bond Values at Call Dates
    #
    # B(t,T)
    #
    # = FV * P(t,T)
    # ========================================================

    straight_bond_values = (

        maturity_value
        *
        cumulative_df[
            :,
            maturity_index
        ][:, np.newaxis]
        /
        cumulative_df[
            :,
            call_indices
        ]

    )


    # ========================================================
    # 13. Callable Value Matrix
    # ========================================================

    n_calls = len(
        call_dates
    )

    callable_values = np.zeros(

        (
            n_paths,
            n_calls
        )

    )

    exercise_values = np.zeros(

        (
            n_paths,
            n_calls
        )

    )


    # ========================================================
    # 14. Last Call
    # ========================================================

    last_call = (
        n_calls - 1
    )

    last_straight_value = (

        straight_bond_values[
            :,
            last_call
        ]

    )

    last_call_price = (
        call_prices[
            last_call
        ]
    )


    last_exercise_value = np.maximum(

        last_straight_value
        - last_call_price,

        0.0

    )

    exercise_values[
        :,
        last_call
    ] = last_exercise_value


    callable_values[
        :,
        last_call
    ] = np.minimum(

        last_straight_value,
        last_call_price

    )


    exercised = (

        last_exercise_value
        > 0.0

    )


    exercise_date_index = np.full(

        n_paths,
        -1,
        dtype=int

    )

    exercise_date_index[
        exercised
    ] = last_call


    # ========================================================
    # 15. Backward Induction
    # ========================================================

    for k in range(

        last_call - 1,
        -1,
        -1

    ):

        call_index = (
            call_indices[k]
        )

        next_index = (
            call_indices[k + 1]
        )


        # ----------------------------------------------------
        # Future Callable Bond Value
        # ----------------------------------------------------

        future_value = (

            callable_values[
                :,
                k + 1
            ]

        )


        # ----------------------------------------------------
        # Discount
        # ----------------------------------------------------

        continuation_discount = (

            cumulative_df[
                :,
                next_index
            ]
            /
            cumulative_df[
                :,
                call_index
            ]

        )


        continuation_value = (

            future_value
            * continuation_discount

        )


        # ----------------------------------------------------
        # State
        # ----------------------------------------------------

        x = x_paths[
            :,
            call_index
        ]

        y = y_paths[
            :,
            call_index
        ]


        # ----------------------------------------------------
        # LSM
        # ----------------------------------------------------

        continuation_estimate = (

            _callable_bond_lsm_regression(

                x,
                y,
                continuation_value

            )

        )


        # ----------------------------------------------------
        # Call Decision
        # ----------------------------------------------------

        call_price = (
            call_prices[k]
        )

        exercise_now = (

            call_price
            < continuation_estimate

        )


        # ----------------------------------------------------
        # Exercise Value
        # ----------------------------------------------------

        exercise_values[
            :,
            k
        ] = np.maximum(

            continuation_estimate
            - call_price,

            0.0

        )


        # ----------------------------------------------------
        # Callable Bond Value
        # ----------------------------------------------------

        callable_values[
            :,
            k
        ] = np.where(

            exercise_now,

            call_price,

            continuation_value

        )


        # ----------------------------------------------------
        # Exercise Date
        # ----------------------------------------------------

        exercise_date_index[
            exercise_now
        ] = k


    # ========================================================
    # 16. Valuation Date -> First Call
    # ========================================================

    first_call_index = (
        call_indices[0]
    )

    first_callable_value = (

        callable_values[
            :,
            0
        ]

    )

    valuation_to_first_call_discount = (

        cumulative_df[
            :,
            first_call_index
        ]
        /
        cumulative_df[
            :,
            0
        ]

    )

    present_values = (

        first_callable_value
        * valuation_to_first_call_discount

    )


    # ========================================================
    # 17. Callable Bond Price
    # ========================================================

    price = np.mean(
        present_values
    )

    if n_paths > 1:

        standard_error = (

            np.std(
                present_values,
                ddof=1
            )
            / np.sqrt(n_paths)

        )

    else:

        standard_error = 0.0


    # ========================================================
    # 18. Straight Bond Price
    # ========================================================

    straight_bond_present_values = (

        maturity_value
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

    straight_bond_price = np.mean(

        straight_bond_present_values

    )


    # ========================================================
    # 19. Callable Option Value
    # ========================================================

    callable_option_value = (

        straight_bond_price
        - price

    )


    # ========================================================
    # 20. Exercise Statistics
    # ========================================================

    exercise_statistics = []

    for k, date in enumerate(
        call_dates
    ):

        count = np.sum(

            exercise_date_index
            == k

        )

        percentage = (

            count
            / n_paths
            * 100.0

        )

        exercise_statistics.append({

            "Call Date":
                date,

            "Call Price":
                call_prices[k],

            "Exercise Paths":
                int(count),

            "Exercise %":
                percentage

        })


    exercise_df = pd.DataFrame(

        exercise_statistics

    )


    # ========================================================
    # 21. Result
    # ========================================================

    result = {

        "Valuation Date":
            valuation_date,

        "Trade Date":
            trade_date,

        "Maturity":
            maturity,

        "Call Dates":
            call_dates,

        "Notional":
            notional,

        "Zero Coupon Rate":
            zero_coupon_rate,

        "Maturity Value":
            maturity_value,

        "Call Prices":
            call_prices,

        "Straight Bond Price":
            straight_bond_price,

        "Callable Bond Price":
            price,

        "Callable Option Value":
            callable_option_value,

        "Standard Error":
            standard_error,

        "95% CI Lower":
            price
            - 1.96 * standard_error,

        "95% CI Upper":
            price
            + 1.96 * standard_error,

        "Exercise Statistics":
            exercise_df,

        "Path Values":
            present_values,

        "Exercise Date Index":
            exercise_date_index,

        "Exercise Values":
            exercise_values,

        "X Paths":
            x_paths,

        "Y Paths":
            y_paths,

        "Phi Path":
            phi,

        "Short Rate Paths":
            short_rates,

        "Callable Values":
            callable_values,

        "Straight Bond Values":
            straight_bond_values

    }

    return result


# ============================================================
# 22. Print Zero Coupon Callable Bond Result
# ============================================================

def print_zero_coupon_callable_bond_result(
    result
):

    print()

    print("=" * 80)

    print(
        "Zero Coupon Callable Bond"
    )

    print("=" * 80)

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

    print(
        f"Notional             : "
        f"{result['Notional']:,.2f}"
    )

    print(
        f"Zero Coupon Rate     : "
        f"{result['Zero Coupon Rate']:.6%}"
    )

    print(
        f"Maturity Value       : "
        f"{result['Maturity Value']:,.2f}"
    )

    print()

    print(
        f"Straight Bond Price  : "
        f"{result['Straight Bond Price']:,.2f}"
    )

    print(
        f"Callable Bond Price  : "
        f"{result['Callable Bond Price']:,.2f}"
    )

    print(
        f"Callable Option Value: "
        f"{result['Callable Option Value']:,.2f}"
    )

    print()

    print(
        f"Standard Error       : "
        f"{result['Standard Error']:,.4f}"
    )

    print(
        f"95% CI               : "
        f"[{result['95% CI Lower']:,.2f}, "
        f"{result['95% CI Upper']:,.2f}]"
    )

    print()

    print(
        "Call / Exercise Statistics"
    )

    print()

    print(
        result[
            "Exercise Statistics"
        ].to_string(
            index=False
        )
    )