# ============================================================
# SwaptionVolCalib.py
#
# G2++ Calibration using Swaption Volatility Matrix
# KRW CD 3M / Actual365Fixed convention
#
# PERFORMANCE OPTIMIZED DEFAULTS
# ------------------------------------------------------------
# tree_steps                       50 -> 15
# max_iterations                  1000 -> 150
# stationary iterations           100 -> 30
# optimizer tolerances            1e-8 -> 1e-6
# initial helper pricing test      ON -> OFF
#
# SPARSE MATRIX SUPPORT
# ------------------------------------------------------------
# NaN cells are treated as missing market quotes and skipped.
# Example:
#   20Y x 10Y quote can be added without filling the entire 20Y row.
#
# Existing callers remain compatible.
#
# Main function
# ------------------------------------------------------------
#
# calibrate_g2(...)
#
# returns
#
# model
# result_df
# calibration_info
#
# ============================================================


import math
import traceback

import numpy as np
import pandas as pd
import QuantLib as ql


# ============================================================
# 1. Utility
# ============================================================

def _period_to_years(period_text):

    text = str(
        period_text
    ).strip().upper()

    if text.endswith("Y"):

        return float(
            text[:-1]
        )

    if text.endswith("M"):

        return (
            float(text[:-1])
            / 12.0
        )

    if text.endswith("W"):

        return (
            float(text[:-1])
            / 52.0
        )

    if text.endswith("D"):

        return (
            float(text[:-1])
            / 365.0
        )

    raise ValueError(
        f"지원하지 않는 Period 형식입니다: {period_text}"
    )


# ============================================================
# 2. Tenor String -> QuantLib Period
# ============================================================

def _to_ql_period(value):

    text = str(
        value
    ).strip().upper()

    try:

        return ql.Period(
            text
        )

    except Exception as exc:

        raise ValueError(
            f"QuantLib Period로 변환할 수 없습니다: {value}"
        ) from exc


# ============================================================
# 3. Safe Model Parameters
# ============================================================

def _get_g2_parameters(model):

    params = model.params()

    return {

        "a":
            float(params[0]),

        "sigma":
            float(params[1]),

        "b":
            float(params[2]),

        "eta":
            float(params[3]),

        "rho":
            float(params[4])

    }


# ============================================================
# 4. Validate Initial Parameters
# ============================================================

def _validate_initial_params(
    initial_params
):

    if len(
        initial_params
    ) != 5:

        raise ValueError(

            "initial_params는 "
            "(a, sigma, b, eta, rho) "
            "5개 값이어야 합니다."

        )


    (
        a,
        sigma,
        b,
        eta,
        rho
    ) = map(
        float,
        initial_params
    )


    if a <= 0.0:

        raise ValueError(
            "G2 parameter a는 0보다 커야 합니다."
        )


    if sigma <= 0.0:

        raise ValueError(
            "G2 parameter sigma는 0보다 커야 합니다."
        )


    if b <= 0.0:

        raise ValueError(
            "G2 parameter b는 0보다 커야 합니다."
        )


    if eta <= 0.0:

        raise ValueError(
            "G2 parameter eta는 0보다 커야 합니다."
        )


    if not (
        -0.999 < rho < 0.999
    ):

        raise ValueError(

            "G2 parameter rho는 "
            "-0.999와 0.999 사이여야 합니다."

        )


    return (
        a,
        sigma,
        b,
        eta,
        rho
    )


# ============================================================
# 5. Swaption Matrix Validation
# ============================================================

def _validate_swaption_matrix(
    swaption_vols
):

    if not isinstance(
        swaption_vols,
        pd.DataFrame
    ):

        raise TypeError(
            "swaption_vols는 pandas DataFrame이어야 합니다."
        )


    if swaption_vols.empty:

        raise ValueError(
            "swaption_vols가 비어 있습니다."
        )


    data = (
        swaption_vols
        .copy()
    )


    # --------------------------------------------------------
    # Index / columns normalization
    # --------------------------------------------------------

    data.index = [

        str(x)
        .strip()
        .upper()

        for x in data.index

    ]


    data.columns = [

        str(x)
        .strip()
        .upper()

        for x in data.columns

    ]


    # --------------------------------------------------------
    # Numeric
    #
    # Sparse matrix 허용:
    # NaN은 "시장 quote 없음"으로 취급하고 helper 생성 시 skip.
    # 숫자가 아닌 문자열도 to_numeric(errors="coerce") 후 NaN이
    # 되므로 동일하게 누락 quote로 간주한다.
    # --------------------------------------------------------

    data = data.apply(

        pd.to_numeric,

        errors="coerce"

    )


    # pandas 2.1+ / 2.2+ 호환:
    # new stack implementation에서는 dropna 인자를
    # 명시하면 ValueError가 발생할 수 있으므로
    # NumPy 배열에서 finite 값만 직접 추출한다.
    raw_values = data.to_numpy(
        dtype=float
    )

    valid_values = raw_values[
        np.isfinite(
            raw_values
        )
    ]


    if len(valid_values) == 0:

        raise ValueError(

            "유효한 Swaption Volatility가 없습니다."

        )


    if (
        valid_values <= 0.0
    ).any():

        raise ValueError(

            "입력된 Swaption Volatility는 "
            "모두 0보다 커야 합니다."

        )


    # --------------------------------------------------------
    # Correct maturity ordering
    # --------------------------------------------------------

    rows = sorted(

        data.index,

        key=_period_to_years

    )


    columns = sorted(

        data.columns,

        key=_period_to_years

    )


    data = data.loc[
        rows,
        columns
    ]


    return data


# ============================================================
# 6. Build Floating Index
# ============================================================

def _build_floating_index(
    curve_handle,
    floating_index_tenor="3M"
):

    tenor_text = str(
        floating_index_tenor
    ).strip().upper()


    # ========================================================
    # KRW CD Index
    #
    # KRW CD 3M convention
    # - Currency      : KRW
    # - Calendar      : South Korea
    # - Business Conv.: Modified Following
    # - Day Counter   : Actual / 365 Fixed
    # ========================================================

    if tenor_text != "3M":

        raise ValueError(
            "KRW CD 기반 Swaption calibration에서는 "
            "floating_index_tenor='3M'만 지원합니다."
        )


    calendar = ql.SouthKorea()


    return ql.IborIndex(
        "KRW-CD",
        ql.Period("3M"),
        1,
        ql.KRWCurrency(),
        calendar,
        ql.ModifiedFollowing,
        False,
        ql.Actual365Fixed(),
        curve_handle
    )


# ============================================================
# 7. Build Swaption Helpers
# ============================================================

def _build_swaption_helpers(

    curve,

    swaption_vols,

    fixed_leg_tenor="3M",

    floating_index_tenor="3M",

    fixed_leg_day_counter=None,

    floating_leg_day_counter=None,

    error_type=None

):

    if fixed_leg_day_counter is None:

        fixed_leg_day_counter = (
            ql.Actual365Fixed()
        )


    if floating_leg_day_counter is None:

        floating_leg_day_counter = (
            ql.Actual365Fixed()
        )


    if error_type is None:

        error_type = (
            ql.BlackCalibrationHelper.RelativePriceError
        )


    curve_handle = (
        ql.YieldTermStructureHandle(
            curve
        )
    )


    index = (
        _build_floating_index(

            curve_handle,

            floating_index_tenor

        )
    )


    fixed_tenor = ql.Period(
        str(
            fixed_leg_tenor
        ).strip().upper()
    )


    helpers = []

    metadata = []


    # ========================================================
    # Matrix:
    #
    # rows    = option expiry
    # columns = underlying swap tenor
    # ========================================================

    for expiry in swaption_vols.index:

        for swap_tenor in swaption_vols.columns:

            raw_volatility = (

                swaption_vols.loc[
                    expiry,
                    swap_tenor
                ]

            )


            # Sparse matrix:
            # quote가 없는 Expiry/Tenor 조합은 calibration helper를
            # 만들지 않는다.
            if pd.isna(
                raw_volatility
            ):
                continue


            volatility = float(
                raw_volatility
            )


            option_period = (
                _to_ql_period(
                    expiry
                )
            )


            swap_period = (
                _to_ql_period(
                    swap_tenor
                )
            )


            vol_handle = (
                ql.QuoteHandle(

                    ql.SimpleQuote(
                        volatility
                    )

                )
            )


            helper = (
                ql.SwaptionHelper(

                    option_period,

                    swap_period,

                    vol_handle,

                    index,

                    fixed_tenor,

                    fixed_leg_day_counter,

                    floating_leg_day_counter,

                    curve_handle,

                    error_type

                )
            )


            helpers.append(
                helper
            )


            metadata.append({

                "option_expiry":
                    str(expiry),

                "swap_tenor":
                    str(swap_tenor),

                "market_vol":
                    volatility,

                "helper":
                    helper

            })


    return (
        helpers,
        metadata,
        curve_handle,
        index
    )


# ============================================================
# 8. Set Pricing Engine
# ============================================================

def _set_engine(
    helpers,
    engine
):

    for helper in helpers:

        helper.setPricingEngine(
            engine
        )


# ============================================================
# 9. Initial Helper Pricing Test
#
# calibration 전에 현재 initial G2 parameters에서
# 모든 helper가 실제 가격 계산 가능한지 확인
# ============================================================

def _test_helpers(
    metadata
):

    failures = []


    for item in metadata:

        helper = item[
            "helper"
        ]


        try:

            model_value = float(
                helper.modelValue()
            )


            market_value = float(
                helper.marketValue()
            )


            if not np.isfinite(
                model_value
            ):

                raise RuntimeError(
                    "modelValue가 finite 값이 아닙니다."
                )


            if not np.isfinite(
                market_value
            ):

                raise RuntimeError(
                    "marketValue가 finite 값이 아닙니다."
                )


        except Exception as exc:

            failures.append({

                "option_expiry":
                    item[
                        "option_expiry"
                    ],

                "swap_tenor":
                    item[
                        "swap_tenor"
                    ],

                "market_vol":
                    item[
                        "market_vol"
                    ],

                "error":
                    str(exc)

            })


    if failures:

        failure_df = pd.DataFrame(
            failures
        )


        raise RuntimeError(

            "\nG2++ calibration 시작 전 "
            "일부 SwaptionHelper의 가격 계산에 실패했습니다.\n\n"

            f"{failure_df.to_string(index=False)}"

        )


# ============================================================
# 10. Calibration Result
# ============================================================

def _build_calibration_result(

    metadata,

    calculate_implied_vol=False,

    implied_vol_accuracy=1.0e-6,

    implied_vol_max_evaluations=100,

    implied_vol_min=1.0e-6,

    implied_vol_max=5.0

):

    rows = []


    for item in metadata:

        helper = item[
            "helper"
        ]


        market_value = float(
            helper.marketValue()
        )


        model_value = float(
            helper.modelValue()
        )


        price_error = (
            model_value
            - market_value
        )


        if abs(
            market_value
        ) > 1.0e-14:

            relative_price_error = (

                price_error
                / market_value

            )

        else:

            relative_price_error = np.nan


        model_implied_vol = np.nan


        if calculate_implied_vol:

            try:

                model_implied_vol = (
                    helper.impliedVolatility(

                        model_value,

                        implied_vol_accuracy,

                        implied_vol_max_evaluations,

                        implied_vol_min,

                        implied_vol_max

                    )
                )

            except Exception:

                model_implied_vol = np.nan


        rows.append({

            "Option Expiry":
                item[
                    "option_expiry"
                ],

            "Swap Tenor":
                item[
                    "swap_tenor"
                ],

            "Market Vol":
                item[
                    "market_vol"
                ],

            "Market Value":
                market_value,

            "Model Value":
                model_value,

            "Price Error":
                price_error,

            "Relative Price Error":
                relative_price_error,

            "Model Implied Vol":
                model_implied_vol

        })


    return pd.DataFrame(
        rows
    )


# ============================================================
# 11. Error Statistics
# ============================================================

def _calibration_statistics(
    result_df
):

    price_error = (

        result_df[
            "Price Error"
        ]

        .to_numpy(
            dtype=float
        )

    )


    relative_error = (

        result_df[
            "Relative Price Error"
        ]

        .to_numpy(
            dtype=float
        )

    )


    finite_relative = relative_error[
        np.isfinite(
            relative_error
        )
    ]


    price_mae = float(

        np.mean(
            np.abs(
                price_error
            )
        )

    )


    price_rmse = float(

        np.sqrt(

            np.mean(
                price_error ** 2
            )

        )

    )


    if len(
        finite_relative
    ) > 0:

        relative_mae = float(

            np.mean(
                np.abs(
                    finite_relative
                )
            )

        )


        relative_rmse = float(

            np.sqrt(

                np.mean(
                    finite_relative ** 2
                )

            )

        )

    else:

        relative_mae = np.nan
        relative_rmse = np.nan


    return {

        "price_mae":
            price_mae,

        "price_rmse":
            price_rmse,

        "relative_price_mae":
            relative_mae,

        "relative_price_rmse":
            relative_rmse

    }


# ============================================================
# 12. Print Calibration Summary
# ============================================================

def _print_calibration_summary(

    parameters,

    statistics,

    engine_name,

    result_df

):

    print()

    print(
        "=" * 100
    )

    print(
        "G2++ Calibration Result"
    )

    print(
        "=" * 100
    )

    print()

    print(
        "Calibration Engine :",
        engine_name
    )

    print()

    print(
        "a     =",
        f"{parameters['a']:.10f}"
    )

    print(
        "sigma =",
        f"{parameters['sigma']:.10f}"
    )

    print(
        "b     =",
        f"{parameters['b']:.10f}"
    )

    print(
        "eta   =",
        f"{parameters['eta']:.10f}"
    )

    print(
        "rho   =",
        f"{parameters['rho']:.10f}"
    )

    print()

    print(
        "Price MAE  :",
        statistics[
            "price_mae"
        ]
    )

    print(
        "Price RMSE :",
        statistics[
            "price_rmse"
        ]
    )

    print(
        "Rel MAE    :",
        statistics[
            "relative_price_mae"
        ]
    )

    print(
        "Rel RMSE   :",
        statistics[
            "relative_price_rmse"
        ]
    )

    print()

    display_columns = [

        "Option Expiry",
        "Swap Tenor",
        "Market Vol",
        "Market Value",
        "Model Value",
        "Relative Price Error"

    ]


    print(

        result_df[
            display_columns
        ]

        .to_string(
            index=False
        )

    )

    print()


# ============================================================
# 13. Main Calibration Function
# ============================================================

def calibrate_g2(

    curve,

    swaption_vols,

    today=None,

    fixed_leg_tenor="3M",

    floating_index_tenor="3M",

    fixed_leg_day_counter=None,

    floating_leg_day_counter=None,

    initial_params=(
        0.10,
        0.01,
        0.30,
        0.015,
        -0.70
    ),

    # --------------------------------------------------------
    # Calibration engine
    #
    # TREE를 기본으로 사용.
    #
    # 기존 G2SwaptionEngine의
    # root not bracketed 오류 회피
    # --------------------------------------------------------

    calibration_engine="TREE",

    tree_steps=15,

    # --------------------------------------------------------
    # G2 analytic engine parameters
    #
    # calibration_engine="ANALYTIC" 사용 시에만 사용
    # --------------------------------------------------------

    engine_range=6.0,

    engine_intervals=16,

    # --------------------------------------------------------
    # Optimizer
    # --------------------------------------------------------

    max_iterations=150,

    max_stationary_state_iterations=30,

    root_epsilon=1.0e-6,

    function_epsilon=1.0e-6,

    gradient_norm_epsilon=1.0e-6,

    # --------------------------------------------------------
    # Implied Vol
    # --------------------------------------------------------

    calculate_implied_vol=False,

    implied_vol_accuracy=1.0e-6,

    implied_vol_max_evaluations=100,

    implied_vol_min=1.0e-6,

    implied_vol_max=5.0,

    # --------------------------------------------------------
    # Output / Speed
    # --------------------------------------------------------

    print_result=True,

    # Debug-only diagnostic. False by default for speed.
    run_initial_pricing_test=False

):

    # ========================================================
    # A. Date
    # ========================================================

    if today is None:

        today = (
            ql.Settings
            .instance()
            .evaluationDate
        )


    if not isinstance(
        today,
        ql.Date
    ):

        raise TypeError(
            "today는 QuantLib Date여야 합니다."
        )


    ql.Settings.instance().evaluationDate = (
        today
    )


    # ========================================================
    # B. Curve
    # ========================================================

    if curve is None:

        raise ValueError(
            "curve가 None입니다."
        )


    # ========================================================
    # C. Swaption Matrix
    # ========================================================

    swaption_vols = (
        _validate_swaption_matrix(
            swaption_vols
        )
    )


    # ========================================================
    # D. Initial Parameters
    # ========================================================

    (
        a0,
        sigma0,
        b0,
        eta0,
        rho0

    ) = _validate_initial_params(
        initial_params
    )


    # ========================================================
    # E. Curve Handle
    # ========================================================

    curve_handle = (
        ql.YieldTermStructureHandle(
            curve
        )
    )


    # ========================================================
    # F. G2 Model
    # ========================================================

    model = ql.G2(

        curve_handle,

        a0,

        sigma0,

        b0,

        eta0,

        rho0

    )


    # ========================================================
    # G. Build Swaption Helpers
    # ========================================================

    (

        helpers,

        metadata,

        _,

        index

    ) = _build_swaption_helpers(

        curve=
            curve,

        swaption_vols=
            swaption_vols,

        fixed_leg_tenor=
            fixed_leg_tenor,

        floating_index_tenor=
            floating_index_tenor,

        fixed_leg_day_counter=
            fixed_leg_day_counter,

        floating_leg_day_counter=
            floating_leg_day_counter,

        error_type=
            ql.BlackCalibrationHelper.RelativePriceError

    )


    if len(
        helpers
    ) == 0:

        raise ValueError(
            "생성된 SwaptionHelper가 없습니다."
        )


    # ========================================================
    # H. Pricing Engine
    # ========================================================

    engine_name = str(
        calibration_engine
    ).strip().upper()


    if engine_name == "TREE":

        # ----------------------------------------------------
        # IMPORTANT
        #
        # TreeSwaptionEngine은 G2SwaptionEngine 내부의
        # root search를 사용하지 않으므로
        #
        # RuntimeError:
        # root not bracketed
        #
        # 문제를 회피한다.
        # ----------------------------------------------------

        engine = (
            ql.TreeSwaptionEngine(

                model,

                int(
                    tree_steps
                )

            )
        )


    elif engine_name in (
        "ANALYTIC",
        "G2"
    ):

        engine = (
            ql.G2SwaptionEngine(

                model,

                float(
                    engine_range
                ),

                int(
                    engine_intervals
                )

            )
        )


        engine_name = "ANALYTIC"


    else:

        raise ValueError(

            "calibration_engine은 "
            "'TREE' 또는 'ANALYTIC'이어야 합니다."

        )


    # ========================================================
    # I. Set Engine
    # ========================================================

    _set_engine(
        helpers,
        engine
    )


    # ========================================================
    # J. Initial Pricing Test
    #
    # 성능 최적화:
    # 기존에는 calibration 전에 모든 helper를 한 번씩
    # modelValue()/marketValue() 평가했다.
    # 기본값에서는 생략하고, 디버깅 시에만 실행한다.
    # ========================================================

    if run_initial_pricing_test:

        print()

        print(
            "G2++ Initial Helper Pricing Test..."
        )

        _test_helpers(
            metadata
        )

        print(
            "Initial Helper Pricing Test 완료"
        )


    # ========================================================
    # K. Calibration Optimizer
    # ========================================================

    optimization_method = (
        ql.LevenbergMarquardt(

            root_epsilon,

            function_epsilon,

            gradient_norm_epsilon

        )
    )


    end_criteria = (
        ql.EndCriteria(

            int(
                max_iterations
            ),

            int(
                max_stationary_state_iterations
            ),

            root_epsilon,

            function_epsilon,

            gradient_norm_epsilon

        )
    )


    # ========================================================
    # L. Calibration
    # ========================================================

    print()

    print(
        f"G2++ Calibration 시작 "
        f"(Engine = {engine_name})"
    )

    print(
        f"Number of Swaptions = {len(helpers)}"
    )

    missing_quotes = int(
        swaption_vols.isna().sum().sum()
    )

    if missing_quotes > 0:

        print(
            f"Missing Matrix Quotes = {missing_quotes} "
            f"(skipped)"
        )

    print()


    try:

        model.calibrate(

            helpers,

            optimization_method,

            end_criteria

        )


    except RuntimeError as exc:

        message = str(
            exc
        )


        # ====================================================
        # ANALYTIC engine으로 실행했는데
        # root-not-bracketed이면 TREE fallback
        # ====================================================

        if (
            engine_name == "ANALYTIC"
            and
            "root not bracketed"
            in message.lower()
        ):

            print()

            print(
                "[WARNING]"
            )

            print(

                "G2SwaptionEngine에서 "
                "root not bracketed 오류가 발생했습니다."

            )

            print(

                "TreeSwaptionEngine으로 "
                "자동 재시도합니다."

            )

            print()


            # ------------------------------------------------
            # Model reset
            # ------------------------------------------------

            model = ql.G2(

                curve_handle,

                a0,

                sigma0,

                b0,

                eta0,

                rho0

            )


            engine = (
                ql.TreeSwaptionEngine(

                    model,

                    int(
                        tree_steps
                    )

                )
            )


            _set_engine(
                helpers,
                engine
            )


            if run_initial_pricing_test:

                _test_helpers(
                    metadata
                )


            model.calibrate(

                helpers,

                optimization_method,

                end_criteria

            )


            engine_name = (
                "TREE (fallback)"
            )


        else:

            raise RuntimeError(

                "\nG2++ calibration에 실패했습니다.\n\n"

                f"Engine: {engine_name}\n\n"

                f"QuantLib error:\n"
                f"{message}"

            ) from exc


    # ========================================================
    # M. Parameter Validation after Calibration
    # ========================================================

    parameters = (
        _get_g2_parameters(
            model
        )
    )


    if parameters[
        "a"
    ] <= 0.0:

        raise RuntimeError(
            "Calibration 후 a <= 0 입니다."
        )


    if parameters[
        "sigma"
    ] <= 0.0:

        raise RuntimeError(
            "Calibration 후 sigma <= 0 입니다."
        )


    if parameters[
        "b"
    ] <= 0.0:

        raise RuntimeError(
            "Calibration 후 b <= 0 입니다."
        )


    if parameters[
        "eta"
    ] <= 0.0:

        raise RuntimeError(
            "Calibration 후 eta <= 0 입니다."
        )


    if not (
        -1.0
        <
        parameters[
            "rho"
        ]
        <
        1.0
    ):

        raise RuntimeError(

            "Calibration 후 rho가 "
            "(-1,1) 범위를 벗어났습니다."

        )


    # ========================================================
    # N. Final Pricing
    #
    # Calibration에서 사용한 동일 engine을 유지
    # ========================================================

    result_df = (
        _build_calibration_result(

            metadata,

            calculate_implied_vol=
                calculate_implied_vol,

            implied_vol_accuracy=
                implied_vol_accuracy,

            implied_vol_max_evaluations=
                implied_vol_max_evaluations,

            implied_vol_min=
                implied_vol_min,

            implied_vol_max=
                implied_vol_max

        )
    )


    # ========================================================
    # O. Statistics
    # ========================================================

    statistics = (
        _calibration_statistics(
            result_df
        )
    )


    # ========================================================
    # P. End Criteria
    # ========================================================

    try:

        end_criteria_type = (
            int(
                model.endCriteria()
            )
        )

    except Exception:

        end_criteria_type = None


    # ========================================================
    # Q. Calibration Information
    # ========================================================

    calibration_info = {

        "today":
            today,

        "parameters":
            parameters,

        "initial_parameters": {

            "a":
                a0,

            "sigma":
                sigma0,

            "b":
                b0,

            "eta":
                eta0,

            "rho":
                rho0

        },

        "engine":
            engine_name,

        "tree_steps":
            int(
                tree_steps
            ),

        "run_initial_pricing_test":
            bool(
                run_initial_pricing_test
            ),

        "number_of_helpers":
            len(
                helpers
            ),

        "number_of_matrix_cells":
            int(
                swaption_vols.shape[0]
                * swaption_vols.shape[1]
            ),

        "number_of_missing_quotes":
            int(
                swaption_vols.isna().sum().sum()
            ),

        "price_mae":
            statistics[
                "price_mae"
            ],

        "price_rmse":
            statistics[
                "price_rmse"
            ],

        "relative_price_mae":
            statistics[
                "relative_price_mae"
            ],

        "relative_price_rmse":
            statistics[
                "relative_price_rmse"
            ],

        "end_criteria":
            end_criteria_type,

        "fixed_leg_tenor":
            str(
                fixed_leg_tenor
            ),

        "floating_index_tenor":
            str(
                floating_index_tenor
            )

    }


    # ========================================================
    # R. Print
    # ========================================================

    if print_result:

        _print_calibration_summary(

            parameters=
                parameters,

            statistics=
                statistics,

            engine_name=
                engine_name,

            result_df=
                result_df

        )


    # ========================================================
    # S. Return
    # ========================================================

    return (

        model,

        result_df,

        calibration_info

    )


# ============================================================
# 14. Optional Pretty Print
# ============================================================

def print_g2_calibration_result(

    calibration_result,

    calibration_info

):

    parameters = (
        calibration_info[
            "parameters"
        ]
    )


    print()

    print(
        "=" * 100
    )

    print(
        "G2++ Calibration"
    )

    print(
        "=" * 100
    )

    print()

    print(
        "Engine :",
        calibration_info.get(
            "engine",
            ""
        )
    )

    print()

    print(
        "a     :",
        parameters[
            "a"
        ]
    )

    print(
        "sigma :",
        parameters[
            "sigma"
        ]
    )

    print(
        "b     :",
        parameters[
            "b"
        ]
    )

    print(
        "eta   :",
        parameters[
            "eta"
        ]
    )

    print(
        "rho   :",
        parameters[
            "rho"
        ]
    )

    print()

    print(
        "Price MAE  :",
        calibration_info.get(
            "price_mae"
        )
    )

    print(
        "Price RMSE :",
        calibration_info.get(
            "price_rmse"
        )
    )

    print()

    print(
        calibration_result.to_string(
            index=False
        )
    )

    print()