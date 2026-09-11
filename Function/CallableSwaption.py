# ============================================================
# CallableSwaption_Optimized.py
#
# G2++ Monte Carlo
# + Bermudan Callable Swap
# + Longstaff-Schwartz
#
# OPTIMIZED VERSION
#
# 주요 최적화
# ------------------------------------------------------------
# 1. Date -> Simulation Index 사전 계산
# 2. Path별 cumulative short-rate integral 사전 계산
# 3. Path별 cumulative discount factor 사전 계산
# 4. Call Date별 Swap Value를 NumPy vectorization
# 5. Python의 path x coupon 3중 loop 제거
# 6. LSM regression vectorization
#
# ============================================================

import QuantLib as ql
import numpy as np
import pandas as pd


# ============================================================
# 1. Convert QuantLib Period to months
# ============================================================

def _period_to_months(period):

    period = ql.Period(period)

    if period.units() == ql.Months:

        return period.length()

    elif period.units() == ql.Years:

        return period.length() * 12

    else:

        raise ValueError(
            "현재는 Months / Years 주기만 지원합니다."
        )


# ============================================================
# 2. Build Schedule
# ============================================================

def _build_schedule(
    start_date,
    maturity_date,
    tenor
):

    calendar = ql.TARGET()

    return ql.Schedule(
        start_date,
        maturity_date,
        ql.Period(tenor),
        calendar,
        ql.ModifiedFollowing,
        ql.ModifiedFollowing,
        ql.DateGeneration.Forward,
        False
    )


# ============================================================
# 3. Date -> Simulation Index
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
# 4. Prepare Simulation
#
# 핵심 최적화
#
# 기존:
#
# call date마다
#     path마다
#         np.sum(short_rate_path[...])
#
# 를 수행
#
# 개선:
#
# 각 path에 대해 cumulative integral을 한 번 계산
#
# ============================================================

def _prepare_simulation(
    simulation
):

    short_rates = np.asarray(
        simulation["short_rate"],
        dtype=float
    )

    times = np.asarray(
        simulation["times"],
        dtype=float
    )

    n_paths, n_times = short_rates.shape

    dt = float(
        times[1] - times[0]
    )

    # --------------------------------------------------------
    # Cumulative integral
    #
    # integral[0] = 0
    #
    # integral[i]
    # =
    # integral from 0 to t_i
    #
    # 현재 코드의 discounting 방식과 동일하게
    # 이후 시점의 short rate를 누적
    # --------------------------------------------------------

    cumulative_integral = np.zeros_like(
        short_rates
    )

    cumulative_integral[:, 1:] = np.cumsum(
        short_rates[:, 1:] * dt,
        axis=1
    )

    # --------------------------------------------------------
    # P(0,t)
    # --------------------------------------------------------

    discount_factors = np.exp(
        -cumulative_integral
    )

    return {

        "short_rates":
            short_rates,

        "times":
            times,

        "dt":
            dt,

        "n_paths":
            n_paths,

        "n_times":
            n_times,

        "discount_factors":
            discount_factors

    }


# ============================================================
# 5. Prepare Schedule
#
# 날짜 관련 계산은 valuation 시점에 한 번만 수행
# ============================================================

def _prepare_schedule(
    schedule,
    valuation_date,
    dt,
    day_counter,
    simulation_length,
    trade_date
):

    dates = list(
        schedule
    )

    payment_dates = []
    payment_indices = []
    start_indices = []
    accruals = []

    for i in range(
        1,
        len(dates)
    ):

        start_date = dates[i - 1]
        payment_date = dates[i]

        # Trade Date 이전 / 당일 cashflow 제외
        if payment_date <= trade_date:
            continue

        payment_index = _date_to_simulation_index(
            payment_date,
            valuation_date,
            dt,
            day_counter
        )

        start_index = _date_to_simulation_index(
            start_date,
            valuation_date,
            dt,
            day_counter
        )

        if payment_index < 0:
            continue

        if payment_index >= simulation_length:
            continue

        accrual = day_counter.yearFraction(
            start_date,
            payment_date
        )

        payment_dates.append(
            payment_date
        )

        payment_indices.append(
            payment_index
        )

        start_indices.append(
            start_index
        )

        accruals.append(
            accrual
        )

    return {

        "dates":
            dates,

        "payment_dates":
            payment_dates,

        "payment_indices":
            np.asarray(
                payment_indices,
                dtype=int
            ),

        "start_indices":
            np.asarray(
                start_indices,
                dtype=int
            ),

        "accruals":
            np.asarray(
                accruals,
                dtype=float
            )

    }


# ============================================================
# 6. Call Date Index
# ============================================================

def _prepare_call_dates(
    call_dates,
    valuation_date,
    trade_date,
    maturity,
    dt,
    day_counter,
    simulation_length
):

    if len(call_dates) == 0:

        raise ValueError(
            "call_dates가 비어 있습니다."
        )

    call_dates = sorted(
        call_dates
    )

    call_indices = []

    for date in call_dates:

        if date <= trade_date:

            raise ValueError(
                f"Call Date {date}는 "
                "trade_date보다 이후여야 합니다."
            )

        if date >= maturity:

            raise ValueError(
                f"Call Date {date}는 "
                "maturity보다 이전이어야 합니다."
            )

        if date <= valuation_date:

            raise ValueError(
                f"Call Date {date}는 "
                "valuation_date 이후여야 합니다."
            )

        index = _date_to_simulation_index(
            date,
            valuation_date,
            dt,
            day_counter
        )

        if index < 0:

            raise ValueError(
                f"Call Date {date}가 "
                "valuation_date보다 빠릅니다."
            )

        if index >= simulation_length:

            raise ValueError(
                f"Call Date {date}가 "
                "Simulation 기간을 초과합니다."
            )

        call_indices.append(
            index
        )

    return (
        call_dates,
        np.asarray(
            call_indices,
            dtype=int
        )
    )


# ============================================================
# 7. Calculate Swap Value at One Call Date
#
# 핵심:
#
# 반환값:
#
# shape = (n_paths,)
#
# Python path loop 없음
#
# ============================================================

def _swap_value_at_call_vectorized(

    prepared,

    call_index,

    fixed_data,

    floating_data,

    fixed_rate,

    floating_spread,

    receiver_payer,

    notional

):

    discount_factors = prepared[
        "discount_factors"
    ]

    n_paths = prepared[
        "n_paths"
    ]

    # ========================================================
    # Fixed Leg
    # ========================================================

    fixed_payment_indices = fixed_data[
        "payment_indices"
    ]

    fixed_start_indices = fixed_data[
        "start_indices"
    ]

    fixed_accruals = fixed_data[
        "accruals"
    ]

    # --------------------------------------------------------
    # Call 이후 payment만 선택
    # --------------------------------------------------------

    fixed_mask = (
        fixed_payment_indices
        > call_index
    )

    if np.any(fixed_mask):

        f_pay = fixed_payment_indices[
            fixed_mask
        ]

        f_accrual = fixed_accruals[
            fixed_mask
        ]

        # ----------------------------------------------------
        # P(call,T)
        #
        # = P(0,T) / P(0,call)
        #
        # shape:
        #
        # (n_paths, n_cashflows)
        # ----------------------------------------------------

        discount_call_to_payment = (

            discount_factors[
                :,
                f_pay
            ]

            /

            discount_factors[
                :,
                call_index
            ][:, None]

        )

        fixed_cashflows = (

            fixed_rate
            * f_accrual
            * notional

        )

        fixed_pv = np.sum(

            discount_call_to_payment
            * fixed_cashflows[None, :],

            axis=1

        )

    else:

        fixed_pv = np.zeros(
            n_paths
        )


    # ========================================================
    # Floating Leg
    # ========================================================

    floating_payment_indices = floating_data[
        "payment_indices"
    ]

    floating_start_indices = floating_data[
        "start_indices"
    ]

    floating_accruals = floating_data[
        "accruals"
    ]

    # --------------------------------------------------------
    # Call 이후 payment
    # --------------------------------------------------------

    floating_mask = (
        floating_payment_indices
        > call_index
    )

    if np.any(floating_mask):

        l_pay = floating_payment_indices[
            floating_mask
        ]

        l_start = floating_start_indices[
            floating_mask
        ]

        l_accrual = floating_accruals[
            floating_mask
        ]

        # ----------------------------------------------------
        # P(call,start)
        # ----------------------------------------------------

        p_start = (

            discount_factors[
                :,
                l_start
            ]

            /

            discount_factors[
                :,
                call_index
            ][:, None]

        )

        # ----------------------------------------------------
        # P(call,payment)
        # ----------------------------------------------------

        p_end = (

            discount_factors[
                :,
                l_pay
            ]

            /

            discount_factors[
                :,
                call_index
            ][:, None]

        )

        # ----------------------------------------------------
        # Forward Rate
        #
        # F =
        # [P(call,start)/P(call,end)-1] / tau
        # ----------------------------------------------------

        forward_rates = (

            p_start / p_end - 1.0

        ) / l_accrual[None, :]

        floating_rates = (

            forward_rates
            + floating_spread

        )

        floating_cashflows = (

            floating_rates
            * l_accrual[None, :]
            * notional

        )

        floating_pv = np.sum(

            floating_cashflows
            * p_end,

            axis=1

        )

    else:

        floating_pv = np.zeros(
            n_paths
        )


    # ========================================================
    # Swap Value
    # ========================================================

    if receiver_payer == "RECEIVER":

        # Receive Fixed
        # Pay Floating

        return (
            fixed_pv
            - floating_pv
        )

    elif receiver_payer == "PAYER":

        # Pay Fixed
        # Receive Floating

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
# 8. Exercise Value
# ============================================================

def _call_exercise_value_vectorized(
    swap_values,
    call_right
):

    if call_right == "RECEIVER":

        return np.maximum(
            swap_values,
            0.0
        )

    elif call_right == "PAYER":

        return np.maximum(
            -swap_values,
            0.0
        )

    else:

        raise ValueError(
            "call_right는 "
            "'Receiver' 또는 'Payer'여야 합니다."
        )


# ============================================================
# 9. LSM Regression
# ============================================================

def _lsm_continuation_value(
    state,
    continuation_value
):

    n = len(state)

    if n < 3:

        return np.full(
            n,
            np.mean(
                continuation_value
            )
        )

    # --------------------------------------------------------
    # Basis
    #
    # 1
    # r
    # r^2
    # --------------------------------------------------------

    A = np.column_stack(

        [

            np.ones(n),

            state,

            state * state

        ]

    )

    coefficients = np.linalg.lstsq(

        A,

        continuation_value,

        rcond=None

    )[0]

    return (

        coefficients[0]

        + coefficients[1] * state

        + coefficients[2] * state * state

    )


# ============================================================
# 10. Main Pricing Function
# ============================================================

def price_callable_swaption(

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

    fixed_leg_tenor="3M",

    floating_leg_tenor="3M"

):

    """
    Optimized Bermudan Callable Swaption.

    G2++ Monte Carlo
    +
    Longstaff-Schwartz

    주요 특징
    ----------
    Path x Call x Coupon
    Python loop 제거.

    NumPy vectorization을 사용합니다.
    """

    # ========================================================
    # 1. Validation
    # ========================================================

    simulation_today = simulation[
        "today"
    ]

    if valuation_date != simulation_today:

        raise ValueError(

            "valuation_date와 "
            "simulation['today']가 다릅니다.\n"

            f"valuation_date = {valuation_date}\n"

            f"simulation_today = "
            f"{simulation_today}"

        )

    if trade_date < valuation_date:

        raise ValueError(
            "trade_date는 valuation_date보다 "
            "빠를 수 없습니다."
        )

    if maturity <= trade_date:

        raise ValueError(
            "maturity는 trade_date보다 "
            "이후여야 합니다."
        )

    # --------------------------------------------------------
    # Position
    # --------------------------------------------------------

    receiver_payer = receiver_payer.upper()

    call_right = call_right.upper()

    if receiver_payer not in [
        "RECEIVER",
        "PAYER"
    ]:

        raise ValueError(
            "receiver_payer는 "
            "'Receiver' 또는 'Payer'여야 합니다."
        )

    if call_right not in [
        "RECEIVER",
        "PAYER"
    ]:

        raise ValueError(
            "call_right는 "
            "'Receiver' 또는 'Payer'여야 합니다."
        )

    # ========================================================
    # 2. Evaluation Date
    # ========================================================

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    # ========================================================
    # 3. Prepare Simulation
    # ========================================================

    prepared = _prepare_simulation(
        simulation
    )

    short_rates = prepared[
        "short_rates"
    ]

    times = prepared[
        "times"
    ]

    dt = prepared[
        "dt"
    ]

    n_paths = prepared[
        "n_paths"
    ]

    n_times = prepared[
        "n_times"
    ]

    discount_factors = prepared[
        "discount_factors"
    ]

    # ========================================================
    # 4. Day Counter
    # ========================================================

    day_counter = ql.Actual365Fixed()

    # ========================================================
    # 5. Call Dates
    # ========================================================

    (
        call_dates,
        call_indices

    ) = _prepare_call_dates(

        call_dates,

        valuation_date,

        trade_date,

        maturity,

        dt,

        day_counter,

        n_times

    )

    n_calls = len(
        call_dates
    )

    # ========================================================
    # 6. Maturity Index
    # ========================================================

    maturity_index = _date_to_simulation_index(

        maturity,

        valuation_date,

        dt,

        day_counter

    )

    if maturity_index >= n_times:

        raise ValueError(
            "Maturity가 Simulation 기간을 "
            "초과합니다."
        )

    # ========================================================
    # 7. Build Schedules
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
    # 8. Prepare Schedules
    # ========================================================

    fixed_data = _prepare_schedule(

        fixed_schedule,

        valuation_date,

        dt,

        day_counter,

        n_times,

        trade_date

    )

    floating_data = _prepare_schedule(

        floating_schedule,

        valuation_date,

        dt,

        day_counter,

        n_times,

        trade_date

    )

    # ========================================================
    # 9. Exercise Value Matrix
    #
    # shape:
    #
    # (n_paths, n_calls)
    #
    # ========================================================

    exercise_values = np.zeros(

        (
            n_paths,
            n_calls
        ),

        dtype=float

    )

    # ========================================================
    # 10. Swap Value Matrix
    # ========================================================

    swap_values = np.zeros(

        (
            n_paths,
            n_calls
        ),

        dtype=float

    )

    # ========================================================
    # 11. Calculate Swap Values
    #
    # 중요:
    #
    # 여기에는 path loop가 없습니다.
    #
    # Call Date에 대한 loop만 존재합니다.
    #
    # ========================================================

    for k, call_index in enumerate(
        call_indices
    ):

        swap_value = _swap_value_at_call_vectorized(

            prepared,

            call_index,

            fixed_data,

            floating_data,

            fixed_rate,

            floating_spread,

            receiver_payer,

            notional

        )

        swap_values[
            :,
            k
        ] = swap_value

        exercise_values[
            :,
            k
        ] = _call_exercise_value_vectorized(

            swap_value,

            call_right

        )

    # ========================================================
    # 12. Longstaff-Schwartz
    # ========================================================

    option_values = np.zeros(
        n_paths,
        dtype=float
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
    # 13. Last Call
    # ========================================================

    last_call = n_calls - 1

    last_exercise = exercise_values[
        :,
        last_call
    ]

    option_values[:] = (
        last_exercise
    )

    exercise_now = (
        last_exercise > 0.0
    )

    exercised[
        exercise_now
    ] = True

    exercise_date_index[
        exercise_now
    ] = last_call

    # ========================================================
    # 14. Backward Induction
    # ========================================================

    for k in range(

        last_call - 1,

        -1,

        -1

    ):

        call_index = call_indices[k]

        next_index = call_indices[k + 1]

        alive = ~exercised

        n_alive = np.sum(
            alive
        )

        if n_alive == 0:

            break

        # ----------------------------------------------------
        # Discount continuation value
        #
        # P(call_k, call_{k+1})
        #
        # = P(0,call_{k+1})
        #   /
        #   P(0,call_k)
        # ----------------------------------------------------

        continuation_discount = (

            discount_factors[
                alive,
                next_index
            ]

            /

            discount_factors[
                alive,
                call_index
            ]

        )

        continuation_value = (

            option_values[
                alive
            ]

            * continuation_discount

        )

        # ----------------------------------------------------
        # State
        # ----------------------------------------------------

        state = short_rates[
            alive,
            call_index
        ]

        # ----------------------------------------------------
        # Exercise
        # ----------------------------------------------------

        exercise_value = exercise_values[
            alive,
            k
        ]

        # ----------------------------------------------------
        # Regression
        # ----------------------------------------------------

        continuation_estimate = (

            _lsm_continuation_value(

                state,

                continuation_value

            )

        )

        # ----------------------------------------------------
        # Exercise Decision
        # ----------------------------------------------------

        exercise_now = (

            exercise_value
            >
            continuation_estimate

        )

        alive_indices = np.flatnonzero(
            alive
        )

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
    # 15. Discount Exercise Value to Valuation Date
    # ========================================================

    present_values = np.zeros(
        n_paths,
        dtype=float
    )

    # --------------------------------------------------------
    # Path를 loop할 필요가 없음
    #
    # Call date별로 mask 처리
    # --------------------------------------------------------

    for k, call_index in enumerate(
        call_indices
    ):

        mask = (
            exercise_date_index
            == k
        )

        if not np.any(mask):

            continue

        discount_factor = (

            discount_factors[
                mask,
                call_index
            ]

        )

        present_values[
            mask
        ] = (

            option_values[
                mask
            ]

            * discount_factor

        )

    # ========================================================
    # 16. Monte Carlo Statistics
    # ========================================================

    price = float(
        np.mean(
            present_values
        )
    )

    if n_paths > 1:

        standard_error = float(

            np.std(

                present_values,

                ddof=1

            )

            /

            np.sqrt(
                n_paths
            )

        )

    else:

        standard_error = 0.0

    # ========================================================
    # 17. Exercise Statistics
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
            /
            n_paths
            *
            100.0

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
    # 18. Result
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

        "Call Indices":
            call_indices,

        "Price":
            price,

        "Standard Error":
            standard_error,

        "95% CI Lower":
            price
            -
            1.96
            *
            standard_error,

        "95% CI Upper":
            price
            +
            1.96
            *
            standard_error,

        "Exercise Statistics":
            exercise_df,

        "Path Values":
            present_values,

        "Exercise Date Index":
            exercise_date_index,

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
            notional,

        "Swap Values":
            swap_values,

        "Exercise Values":
            exercise_values

    }

    return result


# ============================================================
# 19. Print Result
# ============================================================

def print_callable_swaption_result(
    result
):

    print()

    print("=" * 75)

    print(
        "Callable Swap / Bermudan Swaption"
    )

    print("=" * 75)

    print()

    print(
        f"Valuation Date      : "
        f"{result['Valuation Date']}"
    )

    print(
        f"Trade Date          : "
        f"{result['Trade Date']}"
    )

    print(
        f"Maturity            : "
        f"{result['Maturity']}"
    )

    print(
        f"Price               : "
        f"{result['Price']:,.6f}"
    )

    print(
        f"Standard Error      : "
        f"{result['Standard Error']:,.6f}"
    )

    print(
        f"95% CI              : "
        f"[{result['95% CI Lower']:,.6f}, "
        f"{result['95% CI Upper']:,.6f}]"
    )

    print()

    print(
        f"Underlying Swap     : "
        f"{result['Receiver / Payer']}"
    )

    print(
        f"Call Right          : "
        f"{result['Call Right']}"
    )

    print(
        f"Fixed Rate          : "
        f"{result['Fixed Rate']:.6%}"
    )

    print(
        f"Floating Spread     : "
        f"{result['Floating Spread']:.6%}"
    )

    print(
        f"Fixed Leg Tenor     : "
        f"{result['Fixed Leg Tenor']}"
    )

    print(
        f"Floating Leg Tenor  : "
        f"{result['Floating Leg Tenor']}"
    )

    print(
        f"Notional            : "
        f"{result['Notional']:,.0f}"
    )

    print()

    print(
        "Exercise Statistics"
    )

    print()

    print(
        result[
            "Exercise Statistics"
        ].to_string(
            index=False
        )
    )