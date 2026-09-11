# ============================================================
# GSimulation.py
#
# Exact G2++ Monte Carlo Simulation
#
# ============================================================
#
# G2++:
#
#     r(t) = phi(t) + x(t) + y(t)
#
#     dx(t) = -a x(t) dt + sigma dW1(t)
#     dy(t) = -b y(t) dt + eta   dW2(t)
#
#     corr(dW1, dW2) = rho
#
#
# 주요 특징
# ------------------------------------------------------------
# 1. G2++ exact phi(t)
#
# 2. Exact OU discretization
#
# 3. Exact integrated OU process
#
#       Ix = integral_t^(t+dt) x(s) ds
#       Iy = integral_t^(t+dt) y(s) ds
#
# 4. Endpoint factor와 integrated factor의
#    joint Gaussian covariance를 정확하게 사용
#
# 5. Exact stochastic short-rate integral
#
#       Ir = integral_t^(t+dt) r(s) ds
#
# 6. Exact interval discount factor
#
#       DF(t,t+dt) = exp(-Ir)
#
# 7. Pathwise cumulative discount factor
#
#       DF(0,T)
#       = product DF(t_i,t_{i+1})
#
# 8. 기존 Callable CMS와 호환되는
#    simulation dictionary 구조 유지
#
#       simulation["times"]
#       simulation["x"]
#       simulation["y"]
#       simulation["short_rate"]
#       simulation["discount_factors"]
#       simulation["curve"]
#       simulation["model"]
#       simulation["today"]
#
# 9. Market Zero Curve vs MC Zero Curve validation
#
# ============================================================


import QuantLib as ql
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# 1. Utility
# ============================================================

def _B(
    k,
    dt
):
    """
    B(k, dt) = (1 - exp(-k*dt)) / k

    k가 매우 작은 경우 numerical stability 처리.
    """

    if abs(k) < 1.0e-12:

        return dt

    return (
        -np.expm1(-k * dt)
        / k
    )


# ============================================================
# 2. G2++ phi(t)
#
# phi(t)
#
# = f(0,t)
#
# + sigma²/(2a²) * (1-exp(-at))²
#
# + eta²/(2b²) * (1-exp(-bt))²
#
# + rho*sigma*eta/(ab)
#   * (1-exp(-at))
#   * (1-exp(-bt))
# ============================================================

def _g2_phi(
    t,
    forward_rate,
    a,
    sigma,
    b,
    eta,
    rho
):

    t = np.asarray(
        t,
        dtype=float
    )

    one_minus_a = (
        -np.expm1(-a * t)
    )

    one_minus_b = (
        -np.expm1(-b * t)
    )

    term_x = (

        sigma ** 2
        / (2.0 * a ** 2)
        * one_minus_a ** 2

    )

    term_y = (

        eta ** 2
        / (2.0 * b ** 2)
        * one_minus_b ** 2

    )

    term_xy = (

        rho
        * sigma
        * eta
        / (a * b)

        * one_minus_a
        * one_minus_b

    )

    return (

        forward_rate
        + term_x
        + term_y
        + term_xy

    )


# ============================================================
# 3. Integrated Variance Adjustment
#
# Integral of convexity adjustment in phi(t)
#
# ============================================================

def _integrated_phi_adjustment(
    t,
    dt,
    a,
    sigma,
    b,
    eta,
    rho
):
    """
    Calculates

        integral_t^(t+dt)
        [phi(s) - f(0,s)] ds

    analytically.
    """

    t = float(t)
    dt = float(dt)

    T = t + dt

    # --------------------------------------------------------
    # Integral of:
    #
    # sigma²/(2a²) * (1-exp(-as))²
    # --------------------------------------------------------

    integral_x = (

        sigma ** 2
        / (2.0 * a ** 2)

        * (

            dt
            - 2.0 * (
                _B(a, T)
                - _B(a, t)
            )

            + (
                _B(2.0 * a, T)
                - _B(2.0 * a, t)
            )

        )

    )

    # --------------------------------------------------------
    # Integral of:
    #
    # eta²/(2b²) * (1-exp(-bs))²
    # --------------------------------------------------------

    integral_y = (

        eta ** 2
        / (2.0 * b ** 2)

        * (

            dt
            - 2.0 * (
                _B(b, T)
                - _B(b, t)
            )

            + (
                _B(2.0 * b, T)
                - _B(2.0 * b, t)
            )

        )

    )

    # --------------------------------------------------------
    # Correlation term
    #
    # rho*sigma*eta/(ab)
    #
    # * (1-exp(-as))
    # * (1-exp(-bs))
    # --------------------------------------------------------

    integral_xy = (

        rho
        * sigma
        * eta
        / (a * b)

        * (

            dt

            - (
                _B(a, T)
                - _B(a, t)
            )

            - (
                _B(b, T)
                - _B(b, t)
            )

            + (
                _B(a + b, T)
                - _B(a + b, t)
            )

        )

    )

    return (
        integral_x
        + integral_y
        + integral_xy
    )


# ============================================================
# 4. Forward Curve
#
# Simulation용 phi(t)의 forward rate를 사전 계산
# ============================================================

def _build_forward_curve(
    curve,
    today,
    times
):

    forward_rates = np.zeros(
        len(times),
        dtype=float
    )

    day_counter = ql.Actual365Fixed()

    # --------------------------------------------------------
    # t = 0
    # --------------------------------------------------------

    if len(times) == 0:

        return forward_rates

    try:

        next_date = (
            today
            + ql.Period(
                1,
                ql.Days
            )
        )

        forward_rates[0] = (

            curve.forwardRate(

                today,
                next_date,

                day_counter,

                ql.Continuous,
                ql.NoFrequency

            ).rate()

        )

    except Exception:

        forward_rates[0] = (

            curve.zeroRate(

                today,
                day_counter,
                ql.Continuous

            ).rate()

        )

    # --------------------------------------------------------
    # t > 0
    # --------------------------------------------------------

    for i in range(
        1,
        len(times)
    ):

        t = float(
            times[i]
        )

        # ----------------------------------------------------
        # QuantLib curve time를 직접 사용
        #
        # 단순히 months = round(t*12)를 사용하지 않는다.
        # ----------------------------------------------------

        try:

            epsilon = min(
                1.0e-5,
                max(
                    1.0e-7,
                    t * 1.0e-4
                )
            )

            forward_rates[i] = (

                curve.forwardRate(

                    max(
                        0.0,
                        t - epsilon
                    ),

                    t + epsilon,

                    ql.Continuous,
                    ql.NoFrequency

                ).rate()

            )

        except Exception:

            # ------------------------------------------------
            # Date 기반 fallback
            # ------------------------------------------------

            months = max(
                1,
                int(
                    round(t * 12.0)
                )
            )

            maturity_date = (

                today
                + ql.Period(
                    months,
                    ql.Months
                )

            )

            next_date = (

                maturity_date
                + ql.Period(
                    1,
                    ql.Days
                )

            )

            forward_rates[i] = (

                curve.forwardRate(

                    maturity_date,
                    next_date,

                    day_counter,

                    ql.Continuous,
                    ql.NoFrequency

                ).rate()

            )

    return forward_rates


# ============================================================
# 5. Exact Integrated OU Covariance
#
# 한 time step [t, t+dt]에서
#
# X = stochastic part of x(t+dt)
# Y = stochastic part of y(t+dt)
# IX = stochastic part of integral x(s) ds
# IY = stochastic part of integral y(s) ds
#
# 를 joint Gaussian으로 생성한다.
#
# ============================================================

def _integrated_ou_covariance(
    dt,
    a,
    sigma,
    b,
    eta,
    rho
):
    """
    Return covariance matrix of

        [X, Y, IX, IY]

    where

        X  = sigma * integral exp(-a(dt-u)) dW1(u)

        Y  = eta   * integral exp(-b(dt-u)) dW2(u)

        IX = sigma * integral
             [1-exp(-a(dt-u))]/a dW1(u)

        IY = eta * integral
             [1-exp(-b(dt-u))]/b dW2(u)

    """

    dt = float(dt)

    Ba = _B(a, dt)
    Bb = _B(b, dt)
    Bab = _B(a + b, dt)

    # --------------------------------------------------------
    # Variance of endpoint x
    # --------------------------------------------------------

    var_x = (

        sigma ** 2
        * (
            1.0
            - np.exp(-2.0 * a * dt)
        )
        / (2.0 * a)

    )

    # --------------------------------------------------------
    # Variance of endpoint y
    # --------------------------------------------------------

    var_y = (

        eta ** 2
        * (
            1.0
            - np.exp(-2.0 * b * dt)
        )
        / (2.0 * b)

    )

    # --------------------------------------------------------
    # Cov(X,Y)
    # --------------------------------------------------------

    cov_xy = (

        rho
        * sigma
        * eta
        * Bab

    )

    # --------------------------------------------------------
    # Variance of integrated x
    #
    # sigma²/a² *
    #
    # [dt - 2B(a,dt) + B(2a,dt)]
    # --------------------------------------------------------

    var_ix = (

        sigma ** 2
        / a ** 2

        * (

            dt
            - 2.0 * Ba
            + _B(2.0 * a, dt)

        )

    )

    # --------------------------------------------------------
    # Variance of integrated y
    # --------------------------------------------------------

    var_iy = (

        eta ** 2
        / b ** 2

        * (

            dt
            - 2.0 * Bb
            + _B(2.0 * b, dt)

        )

    )

    # --------------------------------------------------------
    # Cov(X, IX)
    # --------------------------------------------------------

    cov_x_ix = (

        sigma ** 2
        / (2.0 * a ** 2)

        * (
            1.0
            - np.exp(-a * dt)
        ) ** 2

    )

    # --------------------------------------------------------
    # Cov(Y, IY)
    # --------------------------------------------------------

    cov_y_iy = (

        eta ** 2
        / (2.0 * b ** 2)

        * (
            1.0
            - np.exp(-b * dt)
        ) ** 2

    )

    # --------------------------------------------------------
    # Cov(X, IY)
    #
    # rho*sigma*eta/b
    #
    # * [B(a,dt) - B(a+b,dt)]
    # --------------------------------------------------------

    cov_x_iy = (

        rho
        * sigma
        * eta
        / b

        * (
            Ba
            - Bab
        )

    )

    # --------------------------------------------------------
    # Cov(Y, IX)
    # --------------------------------------------------------

    cov_y_ix = (

        rho
        * sigma
        * eta
        / a

        * (
            Bb
            - Bab
        )

    )

    # --------------------------------------------------------
    # Cov(IX,IY)
    #
    # rho*sigma*eta/(ab)
    #
    # * [dt - Ba - Bb + Bab]
    # --------------------------------------------------------

    cov_ix_iy = (

        rho
        * sigma
        * eta
        / (a * b)

        * (
            dt
            - Ba
            - Bb
            + Bab
        )

    )

    covariance = np.array(

        [

            [
                var_x,
                cov_xy,
                cov_x_ix,
                cov_x_iy
            ],

            [
                cov_xy,
                var_y,
                cov_y_ix,
                cov_y_iy
            ],

            [
                cov_x_ix,
                cov_y_ix,
                var_ix,
                cov_ix_iy
            ],

            [
                cov_x_iy,
                cov_y_iy,
                cov_ix_iy,
                var_iy
            ]

        ],

        dtype=float

    )

    # --------------------------------------------------------
    # Numerical symmetry
    # --------------------------------------------------------

    covariance = (
        covariance
        + covariance.T
    ) / 2.0

    return covariance


# ============================================================
# 6. Exact G2++ Simulation
# ============================================================

def simulate_g2_short_rate(

    curve,
    model,

    simulation_years=30,

    n_paths=10000,

    time_step_months=1,

    seed=12345,

    store_factors=True,

    store_integrated_rates=True

):

    """
    Exact G2++ Monte Carlo Simulation.

    Parameters
    ----------
    curve :
        QuantLib YieldTermStructure

    model :
        Calibrated QuantLib G2 model

    simulation_years :
        Simulation horizon

    n_paths :
        Number of Monte Carlo paths

    time_step_months :
        Time step in months

    seed :
        Random seed

    store_factors :
        x/y path 저장 여부

    store_integrated_rates :
        interval short-rate integral 저장 여부


    Returns
    -------
    simulation : dict

        times
        x
        y
        short_rate
        phi
        forward_rates
        discount_factors
        interval_discount_factors
        integrated_short_rate
        curve
        model
        today
        parameters

    """

    # ========================================================
    # 1. Model Parameters
    # ========================================================

    params = model.params()

    a = float(
        params[0]
    )

    sigma = float(
        params[1]
    )

    b = float(
        params[2]
    )

    eta = float(
        params[3]
    )

    rho = float(
        params[4]
    )

    # ========================================================
    # 2. Validation
    # ========================================================

    if a <= 0.0:

        raise ValueError(
            "G2++ parameter a는 0보다 커야 합니다."
        )

    if b <= 0.0:

        raise ValueError(
            "G2++ parameter b는 0보다 커야 합니다."
        )

    if sigma < 0.0:

        raise ValueError(
            "G2++ parameter sigma는 0 이상이어야 합니다."
        )

    if eta < 0.0:

        raise ValueError(
            "G2++ parameter eta는 0 이상이어야 합니다."
        )

    if not -1.0 < rho < 1.0:

        raise ValueError(
            "G2++ parameter rho는 -1과 1 사이여야 합니다."
        )

    if n_paths <= 0:

        raise ValueError(
            "n_paths는 0보다 커야 합니다."
        )

    if simulation_years <= 0.0:

        raise ValueError(
            "simulation_years는 0보다 커야 합니다."
        )

    if time_step_months <= 0:

        raise ValueError(
            "time_step_months는 0보다 커야 합니다."
        )

    # ========================================================
    # 3. Evaluation Date
    # ========================================================

    today = (
        ql.Settings
        .instance()
        .evaluationDate
    )

    # ========================================================
    # 4. Time Grid
    #
    # 기존 Callable CMS와 호환하기 위해
    # uniform time grid 유지
    # ========================================================

    dt = (

        float(time_step_months)
        / 12.0

    )

    n_steps = int(

        round(

            simulation_years
            * 12.0
            / time_step_months

        )

    )

    times = (

        np.arange(
            n_steps + 1,
            dtype=float
        )
        * dt

    )

    # ========================================================
    # 5. Forward Curve
    # ========================================================

    forward_rates = _build_forward_curve(

        curve,
        today,
        times

    )

    # ========================================================
    # 6. phi(t)
    # ========================================================

    phi = _g2_phi(

        times,

        forward_rates,

        a,
        sigma,

        b,
        eta,

        rho

    )

    # ========================================================
    # 7. Exact OU Covariance
    #
    # uniform dt이므로 한 번만 계산
    # ========================================================

    covariance = _integrated_ou_covariance(

        dt,

        a,
        sigma,

        b,
        eta,

        rho

    )

    # Uniform grid에서는 step마다 동일한 OU 계수를 사용한다.
    # 반복 루프 밖에서 한 번만 계산해 대규모 path simulation 비용을 줄인다.
    exp_a = np.exp(-a * dt)
    exp_b = np.exp(-b * dt)
    B_a = _B(a, dt)
    B_b = _B(b, dt)

    curve_discounts = np.asarray(
        [
            float(curve.discount(float(t)))
            for t in times
        ],
        dtype=float
    )

    if (curve_discounts <= 0.0).any():
        invalid_index = int(
            np.flatnonzero(curve_discounts <= 0.0)[0]
        )
        raise ValueError(
            "Curve discount factor가 0 이하입니다. "
            f"t={times[invalid_index]}"
        )

    integrated_phi = np.empty(
        n_steps,
        dtype=float
    )

    for i in range(n_steps):
        integrated_phi[i] = (
            np.log(
                curve_discounts[i]
                / curve_discounts[i + 1]
            )
            + _integrated_phi_adjustment(
                times[i],
                dt,
                a,
                sigma,
                b,
                eta,
                rho
            )
        )

    # --------------------------------------------------------
    # Cholesky
    # --------------------------------------------------------

    try:

        chol = np.linalg.cholesky(
            covariance
        )

    except np.linalg.LinAlgError:

        # ----------------------------------------------------
        # Floating point rounding에 의한 아주 작은
        # negative eigenvalue 방지
        # ----------------------------------------------------

        eigenvalues, eigenvectors = (
            np.linalg.eigh(
                covariance
            )
        )

        eigenvalues = np.maximum(
            eigenvalues,
            0.0
        )

        covariance_psd = (

            eigenvectors
            @ np.diag(eigenvalues)
            @ eigenvectors.T

        )

        covariance_psd = (

            covariance_psd
            + covariance_psd.T

        ) / 2.0

        chol = np.linalg.cholesky(

            covariance_psd
            + np.eye(4) * 1.0e-16

        )

    # ========================================================
    # 8. Random Number Generator
    # ========================================================

    rng = np.random.default_rng(
        seed
    )

    # ========================================================
    # 9. Allocate State Variables
    # ========================================================

    if store_factors:

        x = np.zeros(

            (
                n_paths,
                n_steps + 1
            ),

            dtype=np.float64

        )

        y = np.zeros(

            (
                n_paths,
                n_steps + 1
            ),

            dtype=np.float64

        )

    else:

        # ----------------------------------------------------
        # Callable CMS에서는 x/y가 필요하므로
        # store_factors=False이면 callable valuation과
        # 직접 호환되지 않는다.
        #
        # 그래도 short-rate simulation 자체는 가능하도록
        # 마지막 state만 저장한다.
        # ----------------------------------------------------

        x = np.zeros(

            (
                n_paths,
                n_steps + 1
            ),

            dtype=np.float64

        )

        y = np.zeros(

            (
                n_paths,
                n_steps + 1
            ),

            dtype=np.float64

        )

    # ========================================================
    # 10. Short Rate
    # ========================================================

    short_rate = np.zeros(

        (
            n_paths,
            n_steps + 1
        ),

        dtype=np.float64

    )

    # ========================================================
    # 11. Integrated Short Rate
    #
    # interval_integrated_rate[:, i]
    #
    # = integral_{t_i}^{t_{i+1}} r(s) ds
    # ========================================================

    if store_integrated_rates:

        interval_integrated_rate = np.zeros(

            (
                n_paths,
                n_steps
            ),

            dtype=np.float64

        )

    else:

        interval_integrated_rate = None

    # ========================================================
    # 12. Discount Factors
    #
    # discount_factors[:, i]
    #
    # = exp(
    #       - integral_0^{t_i} r(s) ds
    #   )
    # ========================================================

    discount_factors = np.ones(

        (
            n_paths,
            n_steps + 1
        ),

        dtype=np.float64

    )

    interval_discount_factors = np.ones(

        (
            n_paths,
            n_steps
        ),

        dtype=np.float64

    )

    # ========================================================
    # 13. Initial State
    # ========================================================

    x[:, 0] = 0.0
    y[:, 0] = 0.0

    short_rate[:, 0] = phi[0]

    # ========================================================
    # 14. Monte Carlo
    #
    # 각 step에서
    #
    # [endpoint x,
    #  endpoint y,
    #  integrated x,
    #  integrated y]
    #
    # 의 joint Gaussian을 정확하게 생성
    # ========================================================

    for i in range(
        n_steps
    ):

        # ----------------------------------------------------
        # Current time
        # ----------------------------------------------------

        t = times[i]

        # ----------------------------------------------------
        # 4 independent standard normals
        # ----------------------------------------------------

        z = rng.standard_normal(

            (
                n_paths,
                4
            )

        )

        # ----------------------------------------------------
        # Correlated Gaussian vector
        #
        # z @ chol.T
        #
        # covariance =
        #     chol @ chol.T
        # ----------------------------------------------------

        shocks = (
            z @ chol.T
        )

        dx_noise = shocks[
            :,
            0
        ]

        dy_noise = shocks[
            :,
            1
        ]

        dix_noise = shocks[
            :,
            2
        ]

        diy_noise = shocks[
            :,
            3
        ]

        # ----------------------------------------------------
        # Endpoint OU states
        #
        # x(t+dt)
        #
        # = x(t)e^-adt + noise
        # ----------------------------------------------------

        x_next = (

            x[:, i]
            * exp_a
            + dx_noise

        )

        y_next = (

            y[:, i]
            * exp_b
            + dy_noise

        )

        x[:, i + 1] = x_next
        y[:, i + 1] = y_next

        # ----------------------------------------------------
        # Short rate at t+dt
        # ----------------------------------------------------

        short_rate[:, i + 1] = (

            phi[i + 1]
            + x_next
            + y_next

        )

        # ====================================================
        # Exact Integrated x
        #
        # Integral_t^(t+dt) x(s) ds
        #
        # = x(t) B(a,dt)
        #   + integrated stochastic noise
        # ====================================================

        integrated_x = (

            x[:, i]
            * B_a
            + dix_noise

        )

        # ====================================================
        # Exact Integrated y
        # ====================================================

        integrated_y = (

            y[:, i]
            * B_b
            + diy_noise

        )

        # ====================================================
        # Deterministic integral of phi
        #
        # Integral phi(s) ds
        #
        # = Integral f(0,s) ds
        #   + Integrated convexity adjustment
        #
        # Integral f(0,s) ds
        #
        # = ln[P(0,t) / P(0,t+dt)]
        #
        # therefore this part is taken directly from
        # the market discount curve.
        # ====================================================

        # ====================================================
        # Exact Short Rate Integral
        #
        # Integral r(s) ds
        #
        # = Integral phi(s) ds
        #   + Integral x(s) ds
        #   + Integral y(s) ds
        # ====================================================

        integrated_short_rate = (

            integrated_phi[i]
            + integrated_x
            + integrated_y

        )

        if store_integrated_rates:

            interval_integrated_rate[
                :,
                i
            ] = integrated_short_rate

        # ====================================================
        # Exact Interval Discount Factor
        #
        # DF(t,t+dt)
        #
        # = exp(
        #     - Integral_t^(t+dt) r(s)ds
        #   )
        # ====================================================

        interval_df = np.exp(

            -integrated_short_rate

        )

        interval_discount_factors[
            :,
            i
        ] = interval_df

        # ====================================================
        # Cumulative Discount Factor
        #
        # DF(0,t+dt)
        #
        # = DF(0,t)
        #   * DF(t,t+dt)
        # ====================================================

        discount_factors[
            :,
            i + 1
        ] = (

            discount_factors[
                :,
                i
            ]
            * interval_df

        )

    # ========================================================
    # 15. Result
    # ========================================================

    result = {

        "times":
            times,

        "short_rate":
            short_rate,

        "n_paths":
            n_paths,

        "dt":
            dt,

        "curve":
            curve,

        "model":
            model,

        "today":
            today,

        "phi":
            phi,

        "forward_rates":
            forward_rates,

        "discount_factors":
            discount_factors,

        "interval_discount_factors":
            interval_discount_factors,

        "parameters": {

            "a":
                a,

            "sigma":
                sigma,

            "b":
                b,

            "eta":
                eta,

            "rho":
                rho

        }

    }

    # --------------------------------------------------------
    # Factor Paths
    # --------------------------------------------------------

    if store_factors:

        result["x"] = x
        result["y"] = y

    else:

        # ----------------------------------------------------
        # Callable CMS와의 호환을 위해
        # 현재 구조에서는 실제로 x/y를 계속 보존한다.
        # ----------------------------------------------------

        result["x"] = x
        result["y"] = y

    # --------------------------------------------------------
    # Integrated Short Rate
    # --------------------------------------------------------

    if store_integrated_rates:

        result[
            "integrated_short_rate"
        ] = interval_integrated_rate

    return result


# ============================================================
# 16. Plot Short Rate Paths
# ============================================================

def plot_short_rate_paths(

    simulation,
    n_plot_paths=100

):

    times = simulation[
        "times"
    ]

    short_rate = simulation[
        "short_rate"
    ]

    n_paths = short_rate.shape[0]

    n_plot = min(

        int(n_plot_paths),
        n_paths

    )

    plt.figure(
        figsize=(12, 7)
    )

    for i in range(
        n_plot
    ):

        plt.plot(

            times,

            short_rate[
                i,
                :
            ]
            * 100.0,

            linewidth=0.5,

            alpha=0.15

        )

    plt.xlabel(
        "Time (Years)"
    )

    plt.ylabel(
        "Short Rate (%)"
    )

    plt.title(

        "Exact G2++ Short Rate Monte Carlo "
        f"({n_plot:,} Paths)"

    )

    plt.grid(
        True,
        alpha=0.3
    )

    plt.tight_layout()

    plt.show()


# ============================================================
# 17. Market Zero Curve vs Exact MC Zero Curve
# ============================================================

def print_zero_rate_comparison(

    simulation

):

    times = simulation[
        "times"
    ]

    discount_factors = simulation[
        "discount_factors"
    ]

    curve = simulation[
        "curve"
    ]

    maturities = times[1:]

    # ========================================================
    # Market Discount Factors
    # ========================================================

    market_discount_factors = np.array(

        [

            float(
                curve.discount(
                    float(t)
                )
            )

            for t in maturities

        ]

    )

    # ========================================================
    # Market Zero Rates
    # ========================================================

    market_zero_rates = (

        -np.log(
            market_discount_factors
        )
        / maturities

    )

    # ========================================================
    # MC Discount Factors
    #
    # P_MC(0,T)
    #
    # = E[
    #     exp(
    #       -Integral_0^T r(s)ds
    #     )
    #   ]
    # ========================================================

    mc_discount_factors = np.mean(

        discount_factors[
            :,
            1:
        ],

        axis=0

    )

    # ========================================================
    # MC Zero Rates
    # ========================================================

    mc_zero_rates = (

        -np.log(
            mc_discount_factors
        )
        / maturities

    )

    # ========================================================
    # Error
    # ========================================================

    error = (

        mc_zero_rates
        - market_zero_rates

    )

    abs_error = np.abs(
        error
    )

    relative_error = np.divide(

        error,

        market_zero_rates,

        out=np.full_like(
            error,
            np.nan
        ),

        where=np.abs(
            market_zero_rates
        ) > 1.0e-14

    )

    # ========================================================
    # Monte Carlo Standard Error
    #
    # 각 maturity의 DF estimator에 대한
    # statistical standard error
    # ========================================================

    n_paths = discount_factors.shape[0]

    df_std = np.std(

        discount_factors[
            :,
            1:
        ],

        axis=0,

        ddof=1

    )

    df_standard_error = (

        df_std
        / np.sqrt(
            n_paths
        )

    )

    # --------------------------------------------------------
    # Delta method로 zero rate SE 근사
    #
    # R = -ln(P)/T
    #
    # dR/dP = -1/(T P)
    # --------------------------------------------------------

    zero_rate_standard_error = (

        df_standard_error
        / (
            maturities
            * mc_discount_factors
        )

    )

    result = pd.DataFrame({

        "Maturity":
            maturities,

        "Market DF":
            market_discount_factors,

        "MC DF":
            mc_discount_factors,

        "Market Zero Rate (%)":
            market_zero_rates * 100.0,

        "G2++ MC Zero Rate (%)":
            mc_zero_rates * 100.0,

        "Error (bp)":
            error * 10000.0,

        "Abs Error (bp)":
            abs_error * 10000.0,

        "MC Zero Rate SE (bp)":
            zero_rate_standard_error
            * 10000.0,

        "Relative Error (%)":
            relative_error * 100.0

    })

    # ========================================================
    # Print
    # ========================================================

    print()

    print(
        "=" * 120
    )

    print(
        "Market Zero Curve vs Exact G2++ Monte Carlo Zero Curve"
    )

    print(
        "=" * 120
    )

    print()

    print(

        result.to_string(

            index=False,

            formatters={

                "Maturity":
                    lambda x:
                    f"{x:.2f}Y",

                "Market DF":
                    lambda x:
                    f"{x:.8f}",

                "MC DF":
                    lambda x:
                    f"{x:.8f}",

                "Market Zero Rate (%)":
                    lambda x:
                    f"{x:.6f}%",

                "G2++ MC Zero Rate (%)":
                    lambda x:
                    f"{x:.6f}%",

                "Error (bp)":
                    lambda x:
                    f"{x:.4f}",

                "Abs Error (bp)":
                    lambda x:
                    f"{x:.4f}",

                "MC Zero Rate SE (bp)":
                    lambda x:
                    f"{x:.4f}",

                "Relative Error (%)":
                    lambda x:
                    f"{x:.4f}%"

            }

        )

    )

    # ========================================================
    # Summary
    # ========================================================

    mae = (

        np.mean(
            abs_error
        )
        * 10000.0

    )

    max_error = (

        np.max(
            abs_error
        )
        * 10000.0

    )

    rmse = (

        np.sqrt(
            np.mean(
                error ** 2
            )
        )
        * 10000.0

    )

    avg_relative_error = (

        np.nanmean(
            np.abs(
                relative_error
            )
        )
        * 100.0

    )

    print()

    print(
        f"Average Absolute Error = "
        f"{mae:.4f} bp"
    )

    print(
        f"Maximum Absolute Error = "
        f"{max_error:.4f} bp"
    )

    print(
        f"RMSE                    = "
        f"{rmse:.4f} bp"
    )

    print(
        f"Average Relative Error  = "
        f"{avg_relative_error:.4f}%"
    )

    return result


# ============================================================
# 18. Plot Market Zero Curve vs Exact MC Zero Curve
# ============================================================

def plot_zero_rate_comparison(

    simulation,
    show_market=True,
    show_mc=True,
    show_band=True

):

    times = simulation[
        "times"
    ]

    discount_factors = simulation[
        "discount_factors"
    ]

    curve = simulation[
        "curve"
    ]

    valid_times = times[1:]

    # ========================================================
    # Market
    # ========================================================

    market_df = np.array(

        [

            float(
                curve.discount(
                    float(t)
                )
            )

            for t in valid_times

        ]

    )

    market_zero_rates = (

        -np.log(
            market_df
        )
        / valid_times

    )

    # ========================================================
    # MC
    # ========================================================

    path_df = discount_factors[
        :,
        1:
    ]

    mc_df = np.mean(
        path_df,
        axis=0
    )

    mc_zero_rates = (

        -np.log(
            mc_df
        )
        / valid_times

    )

    # ========================================================
    # Confidence Band
    #
    # 이것은 path DF distribution이 아니라
    # MC estimator의 statistical confidence band.
    # ========================================================

    n_paths = path_df.shape[0]

    df_std = np.std(

        path_df,

        axis=0,

        ddof=1

    )

    df_se = (

        df_std
        / np.sqrt(
            n_paths
        )

    )

    zero_rate_se = (

        df_se
        / (
            valid_times
            * mc_df
        )

    )

    lower_rates = (

        mc_zero_rates
        - 1.96
        * zero_rate_se

    )

    upper_rates = (

        mc_zero_rates
        + 1.96
        * zero_rate_se

    )

    # ========================================================
    # Plot
    # ========================================================

    plt.figure(
        figsize=(12, 7)
    )

    if show_band:

        plt.fill_between(

            valid_times,

            lower_rates * 100.0,

            upper_rates * 100.0,

            alpha=0.20,

            label="95% MC Confidence Interval"

        )

    if show_market:

        plt.plot(

            valid_times,

            market_zero_rates * 100.0,

            linewidth=3.0,

            marker="o",

            markersize=3,

            markevery=max(

                1,

                len(valid_times) // 15

            ),

            label="Market Zero Rate"

        )

    if show_mc:

        plt.plot(

            valid_times,

            mc_zero_rates * 100.0,

            linestyle="--",

            linewidth=2.0,

            label="Exact G2++ MC Zero Rate"

        )

    plt.xlabel(
        "Maturity (Years)"
    )

    plt.ylabel(
        "Zero Rate (%)"
    )

    plt.title(
        "Market Zero Curve vs Exact G2++ MC Zero Curve"
    )

    plt.grid(
        True,
        alpha=0.3
    )

    plt.legend()

    plt.tight_layout()

    plt.show()


# ============================================================
# 19. Plot Zero Rate Error
# ============================================================

def plot_zero_rate_error(

    simulation

):

    result = print_zero_rate_comparison(
        simulation
    )

    maturities = result[
        "Maturity"
    ].values

    error_bp = result[
        "Error (bp)"
    ].values

    plt.figure(
        figsize=(12, 6)
    )

    plt.plot(

        maturities,

        error_bp,

        linewidth=2

    )

    plt.axhline(

        0.0,

        linestyle="--",

        linewidth=1

    )

    plt.xlabel(
        "Maturity (Years)"
    )

    plt.ylabel(
        "Zero Rate Error (bp)"
    )

    plt.title(
        "Exact G2++ Monte Carlo Zero Rate Error"
    )

    plt.grid(
        True,
        alpha=0.3
    )

    plt.tight_layout()

    plt.show()

    print()

    print(
        "=========================================="
    )

    print(
        " Exact G2++ Zero Curve Validation"
    )

    print(
        "=========================================="
    )

    print()

    print(

        f"Mean Absolute Error : "
        f"{np.mean(np.abs(error_bp)):.4f} bp"

    )

    print(

        f"Maximum Error       : "
        f"{np.max(np.abs(error_bp)):.4f} bp"

    )