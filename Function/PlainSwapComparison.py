# ============================================================
# PlainSwapComparison_Optimized.py
#
# G2++ Monte Carlo
# vs
# Bootstrapped Zero Curve
#
# Plain Vanilla Swap
#
# Optimization
# ------------------------------------------------------------
# 1. No path loop
# 2. No coupon/path nested loop
# 3. No repeated QuantLib discountBond per path
# 4. Conditional G2 bond vectorized
# 5. Discount factor vectorized
# 6. All maturities 1Y ~ max_years calculated in one pass
# 7. G2 PV @ Zero Par calculated algebraically
# 8. Zero Curve discount factors cached
#
# ============================================================

import QuantLib as ql
import numpy as np
import pandas as pd


# ============================================================
# 1. Build Schedule
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
        ql.Period(tenor),
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
# 3. G2 Parameters
# ============================================================

def _get_g2_parameters(model):

    params = model.params()

    a = float(params[0])
    sigma = float(params[1])
    b = float(params[2])
    eta = float(params[3])
    rho = float(params[4])

    return a, sigma, b, eta, rho


# ============================================================
# 4. G2 B Function
# ============================================================

def _g2_B(
    mean_reversion,
    tau
):

    tau = np.asarray(
        tau,
        dtype=float
    )

    if abs(mean_reversion) < 1e-12:

        return tau

    return (
        1.0
        - np.exp(
            -mean_reversion * tau
        )
    ) / mean_reversion


# ============================================================
# 5. Zero State Conditional Bond
#
# A(t,T)
#
# QuantLib discountBond()는
# path마다 호출하지 않는다.
#
# 동일한 (t,T)에 대해서는 한 번만 계산한다.
# ============================================================

class _G2BondCache:

    def __init__(self, model):

        self.model = model

        (
            self.a,
            self.sigma,
            self.b,
            self.eta,
            self.rho
        ) = _get_g2_parameters(model)

        self.cache = {}


    def _zero_state_bond(
        self,
        t,
        T
    ):

        key = (
            round(float(t), 12),
            round(float(T), 12)
        )

        if key in self.cache:

            return self.cache[key]

        try:

            value = float(
                self.model.discountBond(
                    float(t),
                    float(T),
                    ql.Array(
                        [0.0, 0.0]
                    )
                )
            )

        except TypeError:

            value = float(
                self.model.discountBond(
                    float(t),
                    float(T),
                    0.0,
                    0.0
                )
            )

        self.cache[key] = value

        return value


    def bond_vectorized(
        self,
        t,
        T,
        x,
        y
    ):

        if T <= t:

            return np.ones_like(x)

        tau = float(T - t)

        bx = _g2_B(
            self.a,
            tau
        )

        by = _g2_B(
            self.b,
            tau
        )

        A = self._zero_state_bond(
            t,
            T
        )

        return (
            A
            * np.exp(
                -bx * x
                -by * y
            )
        )


# ============================================================
# 6. Build Phi Path
# ============================================================

def _build_phi_path(
    simulation
):

    times = np.asarray(
        simulation["times"],
        dtype=float
    )

    curve = simulation["curve"]
    model = simulation["model"]

    (
        a,
        sigma,
        b,
        eta,
        rho
    ) = _get_g2_parameters(model)

    phi = np.empty(
        len(times),
        dtype=float
    )

    for i, t in enumerate(times):

        if t <= 0.0:

            phi[i] = curve.zeroRate(
                0.0001,
                ql.Continuous,
                ql.NoFrequency
            ).rate()

            continue

        forward_rate = curve.forwardRate(
            t,
            t + 1e-5,
            ql.Continuous,
            ql.NoFrequency
        ).rate()

        exp_a = np.exp(
            -a * t
        )

        exp_b = np.exp(
            -b * t
        )

        term_x = (

            sigma ** 2
            /
            (2.0 * a ** 2)
            *
            (1.0 - exp_a) ** 2

        )

        term_y = (

            eta ** 2
            /
            (2.0 * b ** 2)
            *
            (1.0 - exp_b) ** 2

        )

        term_xy = (

            rho
            * sigma
            * eta
            /
            (a * b)
            *
            (1.0 - exp_a)
            *
            (1.0 - exp_b)

        )

        phi[i] = (

            forward_rate
            + term_x
            + term_y
            + term_xy

        )

    return phi


# ============================================================
# 7. Build Short Rate
# ============================================================

def _build_short_rate(
    simulation
):

    x = np.asarray(
        simulation["x"],
        dtype=float
    )

    y = np.asarray(
        simulation["y"],
        dtype=float
    )

    if "short_rate" in simulation:

        return np.asarray(
            simulation["short_rate"],
            dtype=float
        )

    if "phi" in simulation:

        phi = np.asarray(
            simulation["phi"],
            dtype=float
        )

    else:

        phi = _build_phi_path(
            simulation
        )

    return (
        x
        + y
        + phi[np.newaxis, :]
    )


# ============================================================
# 8. Vectorized Discount Factors
#
# 모든 Path에 대해 한 번에 계산
#
# DF(0,t)
# ============================================================

def _build_discount_factors(
    simulation
):

    times = np.asarray(
        simulation["times"],
        dtype=float
    )

    dt = float(
        times[1] - times[0]
    )

    short_rates = _build_short_rate(
        simulation
    )

    n_paths, n_times = short_rates.shape

    cumulative_integral = np.zeros(
        (
            n_paths,
            n_times
        ),
        dtype=float
    )

    cumulative_integral[:, 1:] = np.cumsum(

        0.5
        * (
            short_rates[:, :-1]
            + short_rates[:, 1:]
        )
        * dt,

        axis=1

    )

    discount_factors = np.exp(
        -cumulative_integral
    )

    return (
        discount_factors,
        short_rates
    )


# ============================================================
# 9. Build Coupon Information
#
# 1Y ~ max_years 전체 coupon을 한 번 생성
# ============================================================

def _build_coupon_table(
    valuation_date,
    max_maturity,
    payment_tenor,
    dt,
    day_counter
):

    schedule = _build_schedule(
        valuation_date,
        max_maturity,
        payment_tenor
    )

    dates = list(schedule)

    coupons = []

    for i in range(
        1,
        len(dates)
    ):

        start_date = dates[i - 1]

        payment_date = dates[i]

        accrual = day_counter.yearFraction(
            start_date,
            payment_date
        )

        reset_index = _date_to_simulation_index(
            start_date,
            valuation_date,
            dt,
            day_counter
        )

        payment_index = _date_to_simulation_index(
            payment_date,
            valuation_date,
            dt,
            day_counter
        )

        maturity_year = int(
            np.ceil(
                day_counter.yearFraction(
                    valuation_date,
                    payment_date
                )
            )
        )

        coupons.append({

            "start_date":
                start_date,

            "payment_date":
                payment_date,

            "accrual":
                accrual,

            "reset_index":
                reset_index,

            "payment_index":
                payment_index,

            "maturity_year":
                maturity_year

        })

    return coupons


# ============================================================
# 10. Zero Curve Discount Cache
# ============================================================

def _build_zero_curve_discount_cache(
    curve,
    dates
):

    cache = {}

    for date in dates:

        key = date.serialNumber()

        if key in cache:
            continue

        cache[key] = curve.discount(
            curve.timeFromReference(
                date
            )
        )

    return cache


# ============================================================
# 11. Zero Curve Par Rates
#
# 모든 maturity를 한 번에 계산
# ============================================================

def _zero_curve_all_par_rates(
    curve,
    valuation_date,
    max_years,
    floating_spread,
    notional,
    payment_tenor,
    day_counter
):

    # --------------------------------------------------------
    # 최대 maturity
    # --------------------------------------------------------

    max_maturity = (
        valuation_date
        + ql.Period(
            max_years,
            ql.Years
        )
    )

    # --------------------------------------------------------
    # 전체 schedule
    # --------------------------------------------------------

    schedule = _build_schedule(
        valuation_date,
        max_maturity,
        payment_tenor
    )

    dates = list(
        schedule
    )

    # --------------------------------------------------------
    # Discount factor cache
    # --------------------------------------------------------

    discount_cache = (
        _build_zero_curve_discount_cache(
            curve,
            dates
        )
    )

    cumulative_annuity = 0.0

    cumulative_float_pv = 0.0

    result = {}

    for i in range(
        1,
        len(dates)
    ):

        start_date = dates[i - 1]

        payment_date = dates[i]

        accrual = day_counter.yearFraction(
            start_date,
            payment_date
        )

        df_start = discount_cache[
            start_date.serialNumber()
        ]

        df_end = discount_cache[
            payment_date.serialNumber()
        ]

        forward_rate = (

            df_start / df_end
            - 1.0

        ) / accrual

        floating_rate = (

            forward_rate
            + floating_spread

        )

        coupon_annuity = (

            accrual
            * df_end
            * notional

        )

        coupon_float_pv = (

            floating_rate
            * coupon_annuity

        )

        cumulative_annuity += (
            coupon_annuity
        )

        cumulative_float_pv += (
            coupon_float_pv
        )

        maturity_year = int(
            np.ceil(
                day_counter.yearFraction(
                    valuation_date,
                    payment_date
                )
            )
        )

        if (
            maturity_year >= 1
            and maturity_year <= max_years
        ):

            result[maturity_year] = {

                "par_rate":
                    cumulative_float_pv
                    /
                    cumulative_annuity,

                "annuity":
                    cumulative_annuity,

                "floating_pv":
                    cumulative_float_pv

            }

    return result


# ============================================================
# 12. G2++ Coupon Contribution
#
# 핵심 Vectorization
#
# 특정 coupon에 대해
# 모든 path를 동시에 계산
# ============================================================

def _g2_coupon_contribution(
    bond_cache,
    x_paths,
    y_paths,
    discount_factors,
    times,
    coupon,
    floating_spread
):

    reset_index = coupon[
        "reset_index"
    ]

    payment_index = coupon[
        "payment_index"
    ]

    accrual = coupon[
        "accrual"
    ]

    if reset_index < 0:

        return None

    if payment_index >= len(times):

        return None

    if reset_index >= len(times):

        return None

    t = float(
        times[reset_index]
    )

    T = float(
        times[payment_index]
    )

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    x = x_paths[
        :,
        reset_index
    ]

    y = y_paths[
        :,
        reset_index
    ]

    # --------------------------------------------------------
    # P(t,T | x,y)
    # --------------------------------------------------------

    conditional_bond = (
        bond_cache.bond_vectorized(
            t,
            T,
            x,
            y
        )
    )

    # --------------------------------------------------------
    # Forward
    #
    # P(t,t) = 1
    #
    # F = (1/P(t,T)-1)/tau
    # --------------------------------------------------------

    forward_rate = (

        1.0
        /
        np.maximum(
            conditional_bond,
            1e-16
        )
        - 1.0

    ) / accrual

    floating_rate = (

        forward_rate
        + floating_spread

    )

    # --------------------------------------------------------
    # DF(0,T)
    # --------------------------------------------------------

    discount_factor = (
        discount_factors[
            :,
            payment_index
        ]
    )

    # --------------------------------------------------------
    # Floating PV
    # --------------------------------------------------------

    floating_pv = (

        floating_rate
        * accrual
        * discount_factor

    )

    # --------------------------------------------------------
    # Fixed annuity
    # --------------------------------------------------------

    annuity = (

        accrual
        * discount_factor

    )

    return (
        floating_pv,
        annuity
    )


# ============================================================
# 13. G2++ All Maturity Par Rates
#
# 여기서 가장 큰 속도 개선 발생
#
# 1Y 계산
# 2Y 계산
# ...
# 20Y 계산
#
# 각각 다시 simulation 하지 않는다.
# ============================================================

def _g2_all_par_rates(
    simulation,
    valuation_date,
    max_years,
    floating_spread,
    notional,
    payment_tenor
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

    n_paths = x_paths.shape[0]

    dt = float(
        times[1] - times[0]
    )

    day_counter = ql.Actual365Fixed()

    # --------------------------------------------------------
    # Discount Factors
    # --------------------------------------------------------

    (
        discount_factors,
        short_rates

    ) = _build_discount_factors(
        simulation
    )

    # --------------------------------------------------------
    # Maximum maturity
    # --------------------------------------------------------

    max_maturity = (

        valuation_date
        + ql.Period(
            max_years,
            ql.Years
        )

    )

    # --------------------------------------------------------
    # Coupon table
    # --------------------------------------------------------

    coupons = _build_coupon_table(

        valuation_date,

        max_maturity,

        payment_tenor,

        dt,

        day_counter

    )

    # --------------------------------------------------------
    # Bond Cache
    # --------------------------------------------------------

    bond_cache = _G2BondCache(
        simulation["model"]
    )

    # --------------------------------------------------------
    # Cumulative path PV
    # --------------------------------------------------------

    cumulative_floating_pv = np.zeros(
        n_paths,
        dtype=float
    )

    cumulative_annuity = np.zeros(
        n_paths,
        dtype=float
    )

    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    maturity_results = {}

    for coupon in coupons:

        contribution = (
            _g2_coupon_contribution(

                bond_cache,

                x_paths,

                y_paths,

                discount_factors,

                times,

                coupon,

                floating_spread

            )
        )

        if contribution is None:

            continue

        (
            floating_pv,
            annuity
        ) = contribution

        # ----------------------------------------------------
        # 누적
        # ----------------------------------------------------

        cumulative_floating_pv += (
            floating_pv
            * notional
        )

        cumulative_annuity += (
            annuity
            * notional
        )

        maturity_year = coupon[
            "maturity_year"
        ]

        if (
            maturity_year >= 1
            and maturity_year <= max_years
        ):

            path_par_rate = (

                cumulative_floating_pv
                /
                np.maximum(
                    cumulative_annuity,
                    1e-16
                )

            )

            par_rate = (

                np.mean(
                    cumulative_floating_pv
                )
                /
                np.mean(
                    cumulative_annuity
                )

            )

            if n_paths > 1:

                par_rate_se = (

                    np.std(
                        path_par_rate,
                        ddof=1
                    )
                    /
                    np.sqrt(
                        n_paths
                    )

                )

            else:

                par_rate_se = 0.0

            maturity_results[
                maturity_year
            ] = {

                "par_rate":
                    float(par_rate),

                "par_rate_se":
                    float(par_rate_se),

                "floating_pv":
                    float(
                        np.mean(
                            cumulative_floating_pv
                        )
                    ),

                "annuity":
                    float(
                        np.mean(
                            cumulative_annuity
                        )
                    ),

                "path_par_rate":
                    path_par_rate.copy(),

                # --------------------------------------------
                # 중요:
                # 이후 G2 PV 계산을 위해 저장
                # --------------------------------------------

                "path_floating_pv":
                    cumulative_floating_pv.copy(),

                "path_annuity":
                    cumulative_annuity.copy()

            }

    return (
        maturity_results,
        discount_factors,
        short_rates,
        bond_cache
    )


# ============================================================
# 14. Main Comparison
#
# 기존 compare_plain_swap_g2_vs_zero_curve를 대체
# ============================================================

def compare_plain_swap_g2_vs_zero_curve(

    simulation,

    valuation_date=None,

    max_years=20,

    floating_spread=0.0,

    notional=1.0,

    payment_tenor="3M"

):

    """
    Optimized G2++ Monte Carlo vs Zero Curve.

    주요 특징:
    ----------------------------------------------------------
    1. Path loop 제거
    2. Coupon loop는 전체 기간에서 1회만 수행
    3. 1Y~20Y를 별도 Monte Carlo 계산하지 않음
    4. QuantLib discountBond()를 path마다 호출하지 않음
    5. G2 PV는 par rate 계산 결과를 재활용
    """

    # ========================================================
    # 1. Valuation Date
    # ========================================================

    if valuation_date is None:

        valuation_date = (
            simulation["today"]
        )

    if valuation_date != simulation["today"]:

        raise ValueError(

            "valuation_date와 "
            "simulation['today']가 다릅니다.\n"

            f"valuation_date = "
            f"{valuation_date}\n"

            f"simulation['today'] = "
            f"{simulation['today']}"

        )

    # ========================================================
    # 2. QuantLib Evaluation Date
    # ========================================================

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    # ========================================================
    # 3. Basic Data
    # ========================================================

    curve = simulation["curve"]

    x_paths = np.asarray(
        simulation["x"],
        dtype=float
    )

    n_paths = x_paths.shape[0]

    # ========================================================
    # 4. Day Counter
    # ========================================================

    day_counter = ql.Actual365Fixed()

    # ========================================================
    # 5. G2 All Maturities
    # ========================================================

    (
        g2_results,
        discount_factors,
        short_rates,
        bond_cache

    ) = _g2_all_par_rates(

        simulation,

        valuation_date,

        max_years,

        floating_spread,

        notional,

        payment_tenor

    )

    # ========================================================
    # 6. Zero Curve All Maturities
    # ========================================================

    zero_results = (
        _zero_curve_all_par_rates(

            curve,

            valuation_date,

            max_years,

            floating_spread,

            notional,

            payment_tenor,

            day_counter

        )
    )

    # ========================================================
    # 7. Comparison
    # ========================================================

    results = []

    for maturity_year in range(
        1,
        max_years + 1
    ):

        if maturity_year not in g2_results:

            continue

        if maturity_year not in zero_results:

            continue

        # ----------------------------------------------------
        # Maturity
        # ----------------------------------------------------

        maturity = (

            valuation_date
            + ql.Period(
                maturity_year,
                ql.Years
            )

        )

        # ----------------------------------------------------
        # G2
        # ----------------------------------------------------

        g2 = g2_results[
            maturity_year
        ]

        g2_par_rate = g2[
            "par_rate"
        ]

        g2_par_rate_se = g2[
            "par_rate_se"
        ]

        g2_floating_pv = g2[
            "floating_pv"
        ]

        g2_annuity = g2[
            "annuity"
        ]

        # ----------------------------------------------------
        # Zero
        # ----------------------------------------------------

        zero = zero_results[
            maturity_year
        ]

        zero_par_rate = zero[
            "par_rate"
        ]

        zero_annuity = zero[
            "annuity"
        ]

        zero_floating_pv = zero[
            "floating_pv"
        ]

        # ====================================================
        # G2 PV @ Zero Curve Par
        #
        # Receiver:
        #
        # PV =
        # Floating PV
        # -
        # Fixed Rate * Annuity
        # ====================================================

        g2_path_floating_pv = g2[
            "path_floating_pv"
        ]

        g2_path_annuity = g2[
            "path_annuity"
        ]

        g2_path_pv_at_zero = (

            g2_path_floating_pv
            -
            zero_par_rate
            * g2_path_annuity

        )

        g2_pv_at_zero_rate = float(
            np.mean(
                g2_path_pv_at_zero
            )
        )

        if n_paths > 1:

            g2_pv_se = float(

                np.std(
                    g2_path_pv_at_zero,
                    ddof=1
                )
                /
                np.sqrt(
                    n_paths
                )

            )

        else:

            g2_pv_se = 0.0

        # ====================================================
        # Zero Curve PV @ G2 Par
        # ====================================================

        zero_pv_at_g2_rate = (

            zero_floating_pv
            -
            g2_par_rate
            * zero_annuity

        )

        # ====================================================
        # Difference
        # ====================================================

        difference_bp = (

            g2_par_rate
            -
            zero_par_rate

        ) * 10000.0

        if abs(zero_par_rate) > 1e-12:

            difference_pct = (

                g2_par_rate
                /
                zero_par_rate
                -
                1.0

            ) * 100.0

        else:

            difference_pct = np.nan

        # ====================================================
        # Store
        # ====================================================

        results.append({

            "Maturity":
                maturity_year,

            "Maturity Date":
                maturity,

            "Zero Curve Par Rate":
                zero_par_rate,

            "G2++ MC Par Rate":
                g2_par_rate,

            "Difference (bp)":
                difference_bp,

            "Difference (%)":
                difference_pct,

            "G2++ Par Rate SE":
                g2_par_rate_se,

            "G2++ 95% CI Lower":
                g2_par_rate
                -
                1.96
                *
                g2_par_rate_se,

            "G2++ 95% CI Upper":
                g2_par_rate
                +
                1.96
                *
                g2_par_rate_se,

            "G2++ PV @ Zero Par":
                g2_pv_at_zero_rate,

            "G2++ PV SE":
                g2_pv_se,

            "Zero Curve PV @ G2 Par":
                zero_pv_at_g2_rate,

            "G2++ Floating PV":
                g2_floating_pv,

            "G2++ Fixed Annuity":
                g2_annuity,

            "Zero Curve Fixed Annuity":
                zero_annuity

        })

    # ========================================================
    # 8. DataFrame
    # ========================================================

    comparison_df = pd.DataFrame(
        results
    )

    # ========================================================
    # 9. Result
    # ========================================================

    return {

        "Valuation Date":
            valuation_date,

        "Payment Tenor":
            payment_tenor,

        "Notional":
            notional,

        "Floating Spread":
            floating_spread,

        "Number of Paths":
            n_paths,

        "Comparison":
            comparison_df,

        # ----------------------------------------------------
        # 추가로 보관
        # ----------------------------------------------------

        "Discount Factors":
            discount_factors,

        "Short Rate Paths":
            short_rates,

        "G2 Results":
            g2_results

    }


# ============================================================
# 15. Print Result
# ============================================================

def print_plain_swap_comparison(
    result
):

    print()

    print("=" * 110)

    print(
        "Plain Vanilla Swap"
    )

    print(
        "G2++ Monte Carlo vs Bootstrapped Zero Curve"
    )

    print("=" * 110)

    print()

    print(
        f"Valuation Date : "
        f"{result['Valuation Date']}"
    )

    print(
        f"Payment Tenor  : "
        f"{result['Payment Tenor']}"
    )

    print(
        f"Notional       : "
        f"{result['Notional']:,.0f}"
    )

    print(
        f"Floating Spread: "
        f"{result['Floating Spread']:.6%}"
    )

    print(
        f"Number of Paths: "
        f"{result['Number of Paths']:,}"
    )

    print()

    df = result[
        "Comparison"
    ].copy()

    display_df = df[

        [

            "Maturity",

            "Zero Curve Par Rate",

            "G2++ MC Par Rate",

            "Difference (bp)",

            "Difference (%)",

            "G2++ Par Rate SE",

            "G2++ 95% CI Lower",

            "G2++ 95% CI Upper",

            "G2++ PV @ Zero Par",

            "Zero Curve PV @ G2 Par"

        ]

    ].copy()

    # ========================================================
    # Rate -> %
    # ========================================================

    rate_columns = [

        "Zero Curve Par Rate",

        "G2++ MC Par Rate",

        "G2++ Par Rate SE",

        "G2++ 95% CI Lower",

        "G2++ 95% CI Upper"

    ]

    for column in rate_columns:

        display_df[column] *= 100.0

    # ========================================================
    # Formatting
    # ========================================================

    display_df[
        "Zero Curve Par Rate"
    ] = display_df[
        "Zero Curve Par Rate"
    ].map(
        lambda x:
        f"{x:.6f}%"
    )

    display_df[
        "G2++ MC Par Rate"
    ] = display_df[
        "G2++ MC Par Rate"
    ].map(
        lambda x:
        f"{x:.6f}%"
    )

    display_df[
        "Difference (bp)"
    ] = display_df[
        "Difference (bp)"
    ].map(
        lambda x:
        f"{x:.3f}"
    )

    display_df[
        "Difference (%)"
    ] = display_df[
        "Difference (%)"
    ].map(
        lambda x:
        f"{x:.4f}%"
    )

    display_df[
        "G2++ Par Rate SE"
    ] = display_df[
        "G2++ Par Rate SE"
    ].map(
        lambda x:
        f"{x:.6f}%"
    )

    display_df[
        "G2++ 95% CI Lower"
    ] = display_df[
        "G2++ 95% CI Lower"
    ].map(
        lambda x:
        f"{x:.6f}%"
    )

    display_df[
        "G2++ 95% CI Upper"
    ] = display_df[
        "G2++ 95% CI Upper"
    ].map(
        lambda x:
        f"{x:.6f}%"
    )

    display_df[
        "G2++ PV @ Zero Par"
    ] = display_df[
        "G2++ PV @ Zero Par"
    ].map(
        lambda x:
        f"{x:,.6f}"
    )

    display_df[
        "Zero Curve PV @ G2 Par"
    ] = display_df[
        "Zero Curve PV @ G2 Par"
    ].map(
        lambda x:
        f"{x:,.6f}"
    )

    print(
        display_df.to_string(
            index=False
        )
    )