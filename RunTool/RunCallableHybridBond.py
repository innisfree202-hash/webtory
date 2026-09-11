# -*- coding: utf-8 -*-
r"""
RunCallableHybridBond.py

Excel VBA -> Python 실행 진입점

실행 예:
    C:\\Python\\1.Quantlib\\.venv\\Scripts\\python.exe C:\\Python\\1.Quantlib\\RunTool\\RunCallableHybridBond.py "C:\\Python\\1.Quantlib\\RunTool\\Callable_Hybrid_Bond_Valuation_Batch.xlsm"

프로젝트 구조:
    C:\Python\1.Quantlib
    ├─ .venv
    ├─ Function
    │  ├─ Bootstrapping.py
    │  ├─ SwaptionVolCalib.py
    │  ├─ G2Simulation.py
    │  └─ Callablehybridbond.py
    ├─ Input
    │  ├─ Callable_Hybrid_Bond_Input.xlsm
    │  ├─ Yield_depositSwap.xlsx
    │  └─ swaption_vol_long_format.xlsx
    └─ RunTool
       ├─ RunCallableHybridBond.py
       ├─ Callable_Hybrid_Bond_Valuation_Batch.xlsm
       └─ CallableHybridBond_Result.csv
"""

from __future__ import annotations

import sys
import traceback
import json
from pathlib import Path

import numpy as np
import pandas as pd
import QuantLib as ql


# ============================================================
# 1. Project paths
# ============================================================

# Runner가 있는 폴더
RUN_TOOL_PATH = Path(__file__).resolve().parent

# 실제 프로젝트 Root
# C:\\Python\\1.Quantlib\\RunTool\\RunCallableHybridBond.py
# -> PROJECT_ROOT = C:\\Python\\1.Quantlib
PROJECT_ROOT = RUN_TOOL_PATH.parent

FUNCTION_PATH = PROJECT_ROOT / "Function"
INPUT_PATH = PROJECT_ROOT / "Input"

CURVE_FILE = INPUT_PATH / "Yield_depositSwap.xlsx"
SWAPTION_VOL_FILE = INPUT_PATH / "swaption_vol_long_format.xlsx"

# VBA가 workbook 경로를 argv[1]로 전달하므로 일반 실행에서는 사용되지 않음.
# command-line 인자 없이 실행할 때만 RunTool 폴더의 기본 파일을 사용.
DEFAULT_BOND_FILE = RUN_TOOL_PATH / "Callable_Hybrid_Bond_Valuation_Batch.xlsm"

# 결과 CSV는 RunTool 폴더에 저장
RESULT_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_Result.csv"

CURVE_INPUT_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_CurveInput.csv"
CURVE_RESULT_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_CurveResult.csv"
SWAPTION_INPUT_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_SwaptionInput.csv"
G2_SUMMARY_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_G2Calibration_Summary.csv"
G2_DETAIL_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_G2Calibration_Detail.csv"
SIMULATION_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_Simulation.csv"
EXERCISE_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_Exercise.csv"
COUPON_SCHEDULE_CSV_FILE = RUN_TOOL_PATH / "CallableHybridBond_CouponSchedule.csv"


# ============================================================
# 2. Common model settings
# ============================================================

# KRW CD 3M / Actual365Fixed convention
FIXED_LEG_TENOR = "3M"
FLOATING_INDEX_TENOR = "3M"

FIXED_LEG_DAY_COUNTER = ql.Actual365Fixed()
FLOATING_LEG_DAY_COUNTER = ql.Actual365Fixed()

INITIAL_G2_PARAMS = (
    0.10,   # a
    0.01,   # sigma
    0.30,   # b
    0.015,  # eta
    -0.70,  # rho
)

CALIBRATION_ENGINE = "TREE"
TREE_STEPS = 15

# ------------------------------------------------------------
# Execution mode
#
# FAST  : 반복 테스트용 2,000 paths
# FINAL : 최종 평가용 10,000 paths
# ------------------------------------------------------------

SIMULATION_MODE = "FINAL"

if SIMULATION_MODE.upper() == "FAST":
    N_PATHS = 2_000
elif SIMULATION_MODE.upper() == "FINAL":
    N_PATHS = 10_000
else:
    raise ValueError(
        "SIMULATION_MODE는 'FAST' 또는 'FINAL'이어야 합니다."
    )

SIMULATION_YEARS = 30
TIME_STEP_MONTHS = 1
RANDOM_SEED = 12345

# NumPy 기반 G2++ simulation cache.
# QuantLib curve/model 객체는 pickle하지 않고,
# cache hit 시 curve/model만 재구성한 뒤 대용량 MC arrays를 재사용한다.
CACHE_PATH = RUN_TOOL_PATH / "Cache"
CACHE_PATH.mkdir(
    parents=True,
    exist_ok=True,
)

CACHE_VERSION = 3


# ============================================================
# 3. Validate project structure / sys.path
# ============================================================

if not FUNCTION_PATH.is_dir():
    raise FileNotFoundError(
        "\nFunction 폴더를 찾을 수 없습니다.\n"
        f"확인할 경로:\n{FUNCTION_PATH}\n"
    )

if str(FUNCTION_PATH) not in sys.path:
    sys.path.insert(
        0,
        str(FUNCTION_PATH)
    )

REQUIRED_MODULES = [
    "Bootstrapping.py",
    "SwaptionVolCalib.py",
    "G2Simulation.py",
    "Callablehybridbond.py",
]

missing_modules = [
    filename
    for filename in REQUIRED_MODULES
    if not (FUNCTION_PATH / filename).is_file()
]

if missing_modules:
    raise FileNotFoundError(
        "\n다음 Python 파일을 찾을 수 없습니다:\n\n"
        + "\n".join(
            f"  - {x}"
            for x in missing_modules
        )
        + f"\n\n확인할 폴더:\n{FUNCTION_PATH}"
    )


# ============================================================
# 4. Project imports
# ============================================================

from Bootstrapping import bootstrap_curve_from_excel
from SwaptionVolCalib import calibrate_g2
from G2Simulation import simulate_g2_short_rate

from Callablehybridbond import (
    read_callable_hybrid_bond_excel,
    price_callable_hybrid_bond_batch,
    print_callable_hybrid_bond_result,
)


# ============================================================
# 5. Utility
# ============================================================

def ql_date_to_yyyymmdd(
    date: ql.Date,
) -> str:
    """
    QuantLib Date -> YYYYMMDD string
    """

    if not isinstance(
        date,
        ql.Date
    ):
        raise TypeError(
            "date는 QuantLib Date여야 합니다."
        )

    return (
        f"{date.year():04d}"
        f"{date.month():02d}"
        f"{date.dayOfMonth():02d}"
    )



def _normalize_market_date_series(
    series: pd.Series,
) -> pd.Series:
    """
    Excel Data 열을 YYYYMMDD 문자열로 정규화.
    숫자형 20250102가 epoch timestamp로 오해되지 않도록
    숫자 8자리 값을 먼저 처리한다.
    """

    result = pd.Series(
        index=series.index,
        dtype="object",
    )

    numeric = pd.to_numeric(
        series,
        errors="coerce",
    )

    numeric_as_int = (
        numeric
        .round()
        .astype("Int64")
    )

    numeric_text = (
        numeric_as_int
        .astype(str)
    )

    numeric_mask = (
        numeric.notna()
        &
        numeric_text.str.len().eq(8)
    )

    if numeric_mask.any():
        result.loc[numeric_mask] = (
            numeric_text.loc[numeric_mask]
        )

    remaining = ~numeric_mask

    if remaining.any():

        raw = (
            series.loc[remaining]
            .astype(str)
            .str.strip()
            .str.replace(
                ".0",
                "",
                regex=False,
            )
        )

        compact = (
            raw
            .str.replace("-", "", regex=False)
            .str.replace("/", "", regex=False)
        )

        compact_mask = (
            compact
            .str.fullmatch(
                r"\d{8}",
                na=False,
            )
        )

        if compact_mask.any():
            result.loc[
                compact.index[compact_mask]
            ] = compact.loc[compact_mask]

        datetime_mask = ~compact_mask

        if datetime_mask.any():

            parsed = pd.to_datetime(
                series.loc[
                    datetime_mask.index[
                        datetime_mask
                    ]
                ],
                errors="coerce",
            )

            good = parsed.notna()

            if good.any():
                result.loc[
                    parsed.index[good]
                ] = (
                    parsed.loc[good]
                    .dt.strftime("%Y%m%d")
                )

    return result


def _read_market_input_for_date(
    file_path: str | Path,
    valuation_date: ql.Date,
) -> pd.DataFrame:
    """
    Data 열이 있는 시장데이터 파일에서 평가일 데이터만 반환.
    """
    df = pd.read_excel(
        file_path
    )

    if "Data" not in df.columns:
        return df.copy()

    requested = (
        ql_date_to_yyyymmdd(
            valuation_date
        )
    )

    normalized = (
        _normalize_market_date_series(
            df["Data"]
        )
    )

    selected = df.loc[
        normalized == requested
    ].copy()

    return selected


def _ql_date_to_iso(
    value,
):
    if isinstance(value, ql.Date):
        return (
            f"{value.year():04d}-"
            f"{value.month():02d}-"
            f"{value.dayOfMonth():02d}"
        )
    return value


def _calibration_summary_from_info(
    calibration_info: dict | None,
    model=None,
) -> dict:
    """
    Calibration summary를 CSV/Cache 저장 가능한 scalar dict로 변환.
    """

    info = calibration_info or {}

    params = info.get(
        "parameters",
        {}
    )

    if (
        (not params)
        and
        model is not None
    ):
        p = model.params()
        params = {
            "a": float(p[0]),
            "sigma": float(p[1]),
            "b": float(p[2]),
            "eta": float(p[3]),
            "rho": float(p[4]),
        }

    initial = info.get(
        "initial_parameters",
        {
            "a": INITIAL_G2_PARAMS[0],
            "sigma": INITIAL_G2_PARAMS[1],
            "b": INITIAL_G2_PARAMS[2],
            "eta": INITIAL_G2_PARAMS[3],
            "rho": INITIAL_G2_PARAMS[4],
        },
    )

    return {
        "a": params.get("a"),
        "sigma": params.get("sigma"),
        "b": params.get("b"),
        "eta": params.get("eta"),
        "rho": params.get("rho"),
        "initial_a": initial.get("a"),
        "initial_sigma": initial.get("sigma"),
        "initial_b": initial.get("b"),
        "initial_eta": initial.get("eta"),
        "initial_rho": initial.get("rho"),
        "engine": info.get(
            "engine",
            CALIBRATION_ENGINE,
        ),
        "tree_steps": info.get(
            "tree_steps",
            TREE_STEPS,
        ),
        "price_mae": info.get("price_mae"),
        "price_rmse": info.get("price_rmse"),
        "relative_price_mae":
            info.get("relative_price_mae"),
        "relative_price_rmse":
            info.get("relative_price_rmse"),
        "number_of_helpers":
            info.get("number_of_helpers"),
        "number_of_matrix_cells":
            info.get("number_of_matrix_cells"),
        "number_of_missing_quotes":
            info.get("number_of_missing_quotes"),
        "end_criteria":
            info.get("end_criteria"),
        "fixed_leg_tenor":
            info.get(
                "fixed_leg_tenor",
                FIXED_LEG_TENOR,
            ),
        "floating_index_tenor":
            info.get(
                "floating_index_tenor",
                FLOATING_INDEX_TENOR,
            ),
    }


def _cache_aux_files(
    valuation_date: ql.Date,
) -> tuple[Path, Path]:
    key = _cache_key(
        valuation_date
    )
    return (
        CACHE_PATH / f"{key}_calibration.csv",
        CACHE_PATH / f"{key}_calibration_summary.json",
    )

def get_valuation_date_from_bond_excel(
    excel_path: str | Path,
) -> ql.Date:
    """
    Valuation 시트에서 Evaluate? = TRUE인 상품의
    공통 Valuation Date를 읽는다.
    """

    records = read_callable_hybrid_bond_excel(
        str(excel_path),
        sheet_name="Valuation",
    )

    active = [
        rec
        for rec in records
        if rec.get(
            "Evaluate?",
            True
        )
    ]

    if not active:
        raise ValueError(
            "Valuation 시트에 Evaluate? = TRUE인 상품이 없습니다."
        )

    valuation_date = active[0].get(
        "Valuation Date"
    )

    if not isinstance(
        valuation_date,
        ql.Date
    ):
        raise TypeError(
            "Valuation Date를 QuantLib Date로 읽지 못했습니다."
        )

    for rec in active[1:]:

        other_date = rec.get(
            "Valuation Date"
        )

        if other_date != valuation_date:
            raise ValueError(
                "한 번의 Batch 평가에서는 모든 Bond의 "
                "Valuation Date가 동일해야 합니다."
            )

    return valuation_date


# ============================================================
# 6. Swaption volatility Excel
#
# Input long format:
#   옵션만기 / 기초자산만기 / 변동성
#
# calibrate_g2() input:
#   matrix
#   rows    = option expiry
#   columns = underlying swap tenor
# ============================================================

def load_swaption_volatility_matrix(
    file_path: str | Path,
    valuation_date: ql.Date,
) -> pd.DataFrame:
    """
    Swaption volatility long-format Excel을 읽어
    valuation_date에 해당하는 matrix로 변환한다.

    Supported columns
    -----------------
    Required:
        옵션만기
        기초자산만기
        변동성

    Optional but recommended when multiple dates exist:
        Data

    Data가 존재하면 valuation_date(YYYYMMDD)와 동일한 날짜만 선택한 뒤
    pivot한다.
    """

    file_path = Path(
        file_path
    )

    if not file_path.is_file():
        raise FileNotFoundError(
            "Swaption Vol 입력파일을 찾을 수 없습니다:\n"
            f"{file_path}"
        )

    df = pd.read_excel(
        file_path
    )

    required_columns = {
        "옵션만기",
        "기초자산만기",
        "변동성",
    }

    missing = (
        required_columns
        -
        set(df.columns)
    )

    if missing:
        raise ValueError(
            "Swaption Vol Excel에 필요한 열이 없습니다:\n"
            + "\n".join(
                f"  - {x}"
                for x in sorted(missing)
            )
        )

    # --------------------------------------------------------
    # 1. Date filtering
    # --------------------------------------------------------

    requested_date = (
        f"{valuation_date.year():04d}"
        f"{valuation_date.month():02d}"
        f"{valuation_date.dayOfMonth():02d}"
    )

    if "Data" in df.columns:

        raw_date = df["Data"]

        normalized_date = pd.Series(
            index=df.index,
            dtype="object"
        )

        # ----------------------------------------------------
        # 숫자형 YYYYMMDD를 먼저 처리
        #
        # 예:
        #   20250102
        #   20250102.0
        #
        # pd.to_datetime(20250102)는 나노초 epoch로 해석되어
        # 1970-01-01이 되므로 절대 먼저 to_datetime 하지 않는다.
        # ----------------------------------------------------

        numeric_values = pd.to_numeric(
            raw_date,
            errors="coerce"
        )

        numeric_mask = (
            numeric_values.notna()
            &
            (
                numeric_values
                .round()
                .astype("Int64")
                .astype(str)
                .str.len()
                == 8
            )
        )

        if numeric_mask.any():

            normalized_date.loc[
                numeric_mask
            ] = (
                numeric_values.loc[
                    numeric_mask
                ]
                .round()
                .astype("Int64")
                .astype(str)
            )

        # ----------------------------------------------------
        # 나머지는 문자열/Excel 날짜로 처리
        # ----------------------------------------------------

        remaining_mask = ~numeric_mask

        if remaining_mask.any():

            raw_remaining = raw_date.loc[
                remaining_mask
            ]

            # 먼저 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD 문자열 정리
            cleaned = (
                raw_remaining
                .astype(str)
                .str.strip()
                .str.replace(
                    ".0",
                    "",
                    regex=False
                )
            )

            compact = (
                cleaned
                .str.replace(
                    "-",
                    "",
                    regex=False
                )
                .str.replace(
                    "/",
                    "",
                    regex=False
                )
            )

            compact_mask = (
                compact
                .str.fullmatch(
                    r"\d{8}",
                    na=False
                )
            )

            if compact_mask.any():

                normalized_date.loc[
                    compact.index[
                        compact_mask
                    ]
                ] = compact.loc[
                    compact_mask
                ]

            # 실제 날짜형 문자열/Excel datetime
            datetime_mask = ~compact_mask

            if datetime_mask.any():

                dt_source = raw_remaining.loc[
                    datetime_mask
                ]

                parsed = pd.to_datetime(
                    dt_source,
                    errors="coerce"
                )

                good = parsed.notna()

                if good.any():

                    normalized_date.loc[
                        parsed.index[
                            good
                        ]
                    ] = (
                        parsed.loc[
                            good
                        ]
                        .dt.strftime(
                            "%Y%m%d"
                        )
                    )

        df = df.copy()

        df[
            "_NormalizedData"
        ] = normalized_date

        available_dates = sorted(
            {
                str(x)
                for x in df[
                    "_NormalizedData"
                ].dropna()
                if str(x).strip()
            }
        )

        df = df[
            df[
                "_NormalizedData"
            ] == requested_date
        ].copy()

        if df.empty:
            raise ValueError(
                "Swaption Vol Excel에 "
                f"{requested_date} 날짜 데이터가 없습니다.\n"
                "사용 가능한 날짜:\n"
                + ", ".join(
                    available_dates
                )
            )

        print(
            f"Swaption Vol Date = {requested_date}"
        )

    # --------------------------------------------------------
    # 2. Keep required columns
    # --------------------------------------------------------

    df = df[
        [
            "옵션만기",
            "기초자산만기",
            "변동성",
        ]
    ].copy()

    df["옵션만기"] = (
        df["옵션만기"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["기초자산만기"] = (
        df["기초자산만기"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["변동성"] = pd.to_numeric(
        df["변동성"],
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "옵션만기",
            "기초자산만기",
            "변동성",
        ]
    )

    # --------------------------------------------------------
    # 3. Duplicate validation AFTER date filtering
    # --------------------------------------------------------

    duplicated = df.duplicated(
        subset=[
            "옵션만기",
            "기초자산만기",
        ],
        keep=False,
    )

    if duplicated.any():

        dup = df.loc[
            duplicated,
            [
                "옵션만기",
                "기초자산만기",
                "변동성",
            ]
        ]

        raise ValueError(
            "동일 날짜 안에서도 Swaption Vol Excel에 동일한 "
            "옵션만기/기초자산만기 조합이 중복되어 있습니다.\n\n"
            + dup.to_string(
                index=False
            )
        )

    # --------------------------------------------------------
    # 4. Pivot
    # --------------------------------------------------------

    swaption_matrix = df.pivot(
        index="옵션만기",
        columns="기초자산만기",
        values="변동성",
    )

    swaption_matrix.index.name = None
    swaption_matrix.columns.name = None

    if swaption_matrix.empty:
        raise ValueError(
            "Swaption Volatility Matrix가 비어 있습니다."
        )

    valid_count = int(
        np.isfinite(
            swaption_matrix.to_numpy(
                dtype=float
            )
        ).sum()
    )

    if valid_count == 0:
        raise ValueError(
            "유효한 Swaption Volatility가 없습니다."
        )

    return swaption_matrix



# ============================================================
# 7. G2++ Simulation Cache
# ============================================================

def _file_signature(
    path: str | Path,
) -> dict:
    """
    Input file 변경 여부 확인용 signature.
    """
    path = Path(path)

    stat = path.stat()

    return {
        "name": path.name,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _cache_key(
    valuation_date: ql.Date,
) -> str:

    return (
        f"g2_{ql_date_to_yyyymmdd(valuation_date)}"
        f"_{SIMULATION_MODE.upper()}"
        f"_{N_PATHS}"
        f"p_{SIMULATION_YEARS}y"
        f"_{TIME_STEP_MONTHS}m"
        f"_seed{RANDOM_SEED}"
    )


def _cache_files(
    valuation_date: ql.Date,
) -> tuple[Path, Path]:

    key = _cache_key(
        valuation_date
    )

    return (
        CACHE_PATH / f"{key}.npz",
        CACHE_PATH / f"{key}.json",
    )


def _cache_metadata(
    valuation_date: ql.Date,
) -> dict:

    return {
        "cache_version": CACHE_VERSION,
        "valuation_date":
            ql_date_to_yyyymmdd(
                valuation_date
            ),
        "simulation_mode":
            SIMULATION_MODE.upper(),
        "n_paths":
            int(N_PATHS),
        "simulation_years":
            int(SIMULATION_YEARS),
        "time_step_months":
            int(TIME_STEP_MONTHS),
        "random_seed":
            int(RANDOM_SEED),
        "calibration_engine":
            str(CALIBRATION_ENGINE),
        "tree_steps":
            int(TREE_STEPS),
        "fixed_leg_tenor":
            str(FIXED_LEG_TENOR),
        "floating_index_tenor":
            str(FLOATING_INDEX_TENOR),
        "fixed_leg_day_counter":
            "Actual365Fixed",
        "floating_leg_day_counter":
            "Actual365Fixed",
        "initial_g2_params":
            [float(x) for x in INITIAL_G2_PARAMS],
        "curve_file":
            _file_signature(
                CURVE_FILE
            ),
        "swaption_vol_file":
            _file_signature(
                SWAPTION_VOL_FILE
            ),
    }


def _metadata_matches(
    cached: dict,
    current: dict,
) -> bool:

    return cached == current


def _save_simulation_cache(
    simulation: dict,
    valuation_date: ql.Date,
) -> Path:
    """
    QuantLib 객체는 저장하지 않고 NumPy arrays와 G2 파라미터만 저장한다.
    """

    npz_file, meta_file = (
        _cache_files(
            valuation_date
        )
    )

    model = simulation.get(
        "model"
    )

    if model is None:
        raise ValueError(
            "simulation['model']이 없어 cache를 저장할 수 없습니다."
        )

    params = model.params()

    arrays = {}

    for key in (
        "times",
        "x",
        "y",
        "short_rate",
        "discount_factors",
        "integrated_rates",
        "phi",
        "market_zero_rates",
        "mc_zero_rates",
        "maturities",
    ):

        if key in simulation:

            value = simulation[
                key
            ]

            if value is not None:

                try:
                    arrays[key] = np.asarray(
                        value
                    )
                except Exception:
                    pass

    arrays["_g2_params"] = np.asarray(
        [
            float(params[0]),
            float(params[1]),
            float(params[2]),
            float(params[3]),
            float(params[4]),
        ],
        dtype=float,
    )

    np.savez_compressed(
        npz_file,
        **arrays,
    )

    metadata = (
        _cache_metadata(
            valuation_date
        )
    )

    meta_file.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Calibration detail / summary도 cache에 보관하여
    # cache hit 시 Excel G2Calibration 시트를 재구성한다.
    calibration_csv, calibration_json = (
        _cache_aux_files(
            valuation_date
        )
    )

    calibration_result_df = simulation.get(
        "_calibration_result_df"
    )

    if isinstance(
        calibration_result_df,
        pd.DataFrame
    ):
        calibration_result_df.to_csv(
            calibration_csv,
            index=False,
            encoding="utf-8-sig",
        )

    calibration_summary = simulation.get(
        "_calibration_summary"
    )

    if calibration_summary is None:
        calibration_summary = (
            _calibration_summary_from_info(
                simulation.get(
                    "_calibration_info"
                ),
                model=model,
            )
        )

    calibration_json.write_text(
        json.dumps(
            calibration_summary,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return npz_file


def _load_simulation_cache(
    valuation_date: ql.Date,
) -> dict | None:
    """
    Cache가 유효하면 MC arrays를 불러온다.
    curve/model은 별도로 재구성해야 하므로 여기서는 arrays와 params만 반환.
    """

    npz_file, meta_file = (
        _cache_files(
            valuation_date
        )
    )

    if (
        not npz_file.is_file()
        or
        not meta_file.is_file()
    ):
        return None

    try:

        cached_meta = json.loads(
            meta_file.read_text(
                encoding="utf-8"
            )
        )

        current_meta = (
            _cache_metadata(
                valuation_date
            )
        )

        if not _metadata_matches(
            cached_meta,
            current_meta,
        ):
            print(
                "Cache invalidated: "
                "시장데이터 또는 모델 설정이 변경되었습니다."
            )
            return None

        data = np.load(
            npz_file,
            allow_pickle=False,
        )

        result = {
            key: data[key]
            for key in data.files
            if key != "_g2_params"
        }

        result["_g2_params"] = (
            np.asarray(
                data["_g2_params"],
                dtype=float,
            )
        )

        result["_cache_file"] = (
            str(
                npz_file
            )
        )

        calibration_csv, calibration_json = (
            _cache_aux_files(
                valuation_date
            )
        )

        if calibration_csv.is_file():
            result[
                "_calibration_result_df"
            ] = pd.read_csv(
                calibration_csv
            )

        if calibration_json.is_file():
            result[
                "_calibration_summary"
            ] = json.loads(
                calibration_json.read_text(
                    encoding="utf-8"
                )
            )

        return result

    except Exception as exc:

        print(
            "Cache load 실패 -> 새 Simulation을 생성합니다."
        )

        print(
            f"Cache Error: {exc}"
        )

        return None


def build_g2_curve_and_model(
    valuation_date: ql.Date,
) -> tuple:
    """
    Cache hit 시에도 Callablehybridbond가 필요로 하는
    QuantLib curve/model 객체는 가볍게 재구성한다.

    Monte Carlo simulation은 수행하지 않는다.
    """

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    calibration_date = (
        ql_date_to_yyyymmdd(
            valuation_date
        )
    )

    print()
    print("=" * 80)
    print("1. Yield Curve Bootstrapping (Cache Restore)")
    print("=" * 80)

    curve, curve_result_df, curve_info = (
        bootstrap_curve_from_excel(
            file_path=str(
                CURVE_FILE
            ),
            calibration_date=calibration_date,
            swap_index_tenor=FLOATING_INDEX_TENOR,
            fixed_leg_tenor=FIXED_LEG_TENOR,
        )
    )

    print()
    print("=" * 80)
    print("2. G2++ Model Restore from Cache")
    print("=" * 80)

    cached = (
        _load_simulation_cache(
            valuation_date
        )
    )

    if cached is None:
        return None

    params = cached[
        "_g2_params"
    ]

    model = ql.G2(
        ql.YieldTermStructureHandle(
            curve
        ),
        float(params[0]),
        float(params[1]),
        float(params[2]),
        float(params[3]),
        float(params[4]),
    )

    cached[
        "curve"
    ] = curve

    cached[
        "model"
    ] = model

    cached[
        "today"
    ] = valuation_date

    cached[
        "_curve_result_df"
    ] = curve_result_df

    cached[
        "_curve_info"
    ] = curve_info

    print(
        f"Cache Loaded: {cached['_cache_file']}"
    )

    print(
        f"Number of Paths: "
        f"{np.asarray(cached['x']).shape[0]:,}"
    )

    print(
        "G2++ Calibration / Monte Carlo Simulation 생략"
    )

    return cached


def get_or_build_g2_simulation(
    valuation_date: ql.Date,
) -> dict:
    """
    Cache hit:
        curve 재구성 + calibrated G2 params 복원 + MC arrays load

    Cache miss:
        기존 full calibration/simulation 수행 + cache 저장
    """

    cached = (
        _load_simulation_cache(
            valuation_date
        )
    )

    if cached is not None:

        restored = (
            build_g2_curve_and_model(
                valuation_date
            )
        )

        if restored is not None:
            return restored

    print()
    print("=" * 80)
    print("G2++ Cache MISS")
    print("=" * 80)

    print(
        f"Simulation Mode = {SIMULATION_MODE.upper()}"
    )

    print(
        f"Number of Paths = {N_PATHS:,}"
    )

    simulation = (
        build_g2_simulation(
            valuation_date
        )
    )

    cache_file = (
        _save_simulation_cache(
            simulation,
            valuation_date
        )
    )

    print()
    print(
        f"Simulation Cache Saved: {cache_file}"
    )

    return simulation


# ============================================================
# 8. Build G2++ simulation
# ============================================================

def build_g2_simulation(
    valuation_date: ql.Date,
) -> dict:

    if not isinstance(
        valuation_date,
        ql.Date
    ):
        raise TypeError(
            "valuation_date는 QuantLib Date여야 합니다."
        )

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    calibration_date = (
        ql_date_to_yyyymmdd(
            valuation_date
        )
    )

    # --------------------------------------------------------
    # 1) Yield Curve Bootstrapping
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("1. Yield Curve Bootstrapping")
    print("=" * 80)

    if not CURVE_FILE.is_file():
        raise FileNotFoundError(
            "Yield Curve 입력파일을 찾을 수 없습니다:\n"
            f"{CURVE_FILE}"
        )

    curve, curve_result_df, curve_info = (
        bootstrap_curve_from_excel(
            file_path=str(
                CURVE_FILE
            ),
            calibration_date=calibration_date,
            swap_index_tenor=FLOATING_INDEX_TENOR,
            fixed_leg_tenor=FIXED_LEG_TENOR,
        )
    )

    if curve is None:
        raise RuntimeError(
            "Bootstrapping 결과 curve가 None입니다."
        )

    print(
        f"Calibration Date = {calibration_date}"
    )

    print(
        f"Number of Curve Instruments = "
        f"{len(curve_result_df):,}"
    )

    print(
        "Yield Curve Bootstrapping 완료"
    )

    # --------------------------------------------------------
    # 2) Swaption Volatility Matrix
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("2. Swaption Volatility Load")
    print("=" * 80)

    swaption_vols = (
        load_swaption_volatility_matrix(
            SWAPTION_VOL_FILE,
            valuation_date=valuation_date,
        )
    )

    n_valid_quotes = int(
        np.isfinite(
            swaption_vols.to_numpy(
                dtype=float
            )
        ).sum()
    )

    print(
        f"Swaption Matrix Shape = "
        f"{swaption_vols.shape[0]} x "
        f"{swaption_vols.shape[1]}"
    )

    print(
        f"Number of Valid Swaption Vols = "
        f"{n_valid_quotes:,}"
    )

    # --------------------------------------------------------
    # 3) G2++ Calibration
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("3. G2++ Calibration")
    print("=" * 80)

    (
        model,
        calibration_result_df,
        calibration_info,
    ) = calibrate_g2(

        curve=curve,

        swaption_vols=swaption_vols,

        today=valuation_date,

        fixed_leg_tenor=
            FIXED_LEG_TENOR,

        floating_index_tenor=
            FLOATING_INDEX_TENOR,

        fixed_leg_day_counter=
            FIXED_LEG_DAY_COUNTER,

        floating_leg_day_counter=
            FLOATING_LEG_DAY_COUNTER,

        initial_params=
            INITIAL_G2_PARAMS,

        calibration_engine=
            CALIBRATION_ENGINE,

        tree_steps=
            TREE_STEPS,

        print_result=True,

        run_initial_pricing_test=False,
    )

    if model is None:
        raise RuntimeError(
            "G2++ Calibration 결과 model이 None입니다."
        )

    print(
        "G2++ Calibration 완료"
    )

    # --------------------------------------------------------
    # 4) Exact G2++ Monte Carlo Simulation
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("4. Exact G2++ Monte Carlo Simulation")
    print("=" * 80)

    simulation = simulate_g2_short_rate(

        curve=curve,

        model=model,

        simulation_years=
            SIMULATION_YEARS,

        n_paths=
            N_PATHS,

        time_step_months=
            TIME_STEP_MONTHS,

        seed=
            RANDOM_SEED,

        store_factors=
            True,

        store_integrated_rates=
            True,
    )

    required_keys = {
        "times",
        "x",
        "y",
        "short_rate",
        "discount_factors",
        "curve",
        "model",
        "today",
    }

    missing_keys = (
        required_keys
        -
        set(
            simulation.keys()
        )
    )

    if missing_keys:
        raise KeyError(
            "G2Simulation 결과에 필요한 key가 없습니다:\n"
            + "\n".join(
                f"  - {x}"
                for x in sorted(
                    missing_keys
                )
            )
        )

    if simulation["today"] != valuation_date:
        raise ValueError(
            "simulation['today']와 "
            "Bond Valuation Date가 일치하지 않습니다.\n"
            f"simulation today : {simulation['today']}\n"
            f"valuation date   : {valuation_date}"
        )

    x = np.asarray(
        simulation["x"]
    )

    print(
        f"Number of Paths      = "
        f"{x.shape[0]:,}"
    )

    print(
        f"Number of Time Steps = "
        f"{len(simulation['times']):,}"
    )

    print(
        f"Simulation Horizon   = "
        f"{SIMULATION_YEARS}Y"
    )

    print(
        f"Random Seed          = "
        f"{RANDOM_SEED}"
    )

    print(
        "Exact G2++ Simulation 완료"
    )

    # Runner 내부 진단용 정보
    simulation[
        "_curve_result_df"
    ] = curve_result_df

    simulation[
        "_curve_info"
    ] = curve_info

    simulation[
        "_calibration_result_df"
    ] = calibration_result_df

    simulation[
        "_calibration_info"
    ] = calibration_info

    simulation[
        "_calibration_summary"
    ] = _calibration_summary_from_info(
        calibration_info,
        model=model,
    )

    return simulation


# ============================================================
# 8. Console output
# ============================================================

def print_batch_results(
    results: dict,
) -> None:

    print()
    print("=" * 80)
    print("5. Callable Hybrid Bond Valuation Results")
    print("=" * 80)

    if not results:

        print(
            "평가된 Bond가 없습니다."
        )

        return

    for (
        bond_name,
        result
    ) in results.items():

        print()
        print("#" * 80)

        print(
            f"Bond Name : {bond_name}"
        )

        print("#" * 80)

        print_callable_hybrid_bond_result(
            result,
            show_coupon_schedule=False,
        )


# ============================================================
# 9. Export summary CSV
#
# Excel이 VBA에서 열려 있을 수 있으므로,
# 같은 xlsm 파일을 Python에서 직접 저장하지 않는다.
#
# Python -> CSV 생성
# VBA    -> CSV를 Excel Result 시트로 Import
# ============================================================


def export_detail_csvs(
    simulation: dict,
    results: dict,
    valuation_date: ql.Date,
) -> dict:
    """
    Excel의 상세 시트에 적재할 CSV들을 생성한다.
    """

    run_timestamp = pd.Timestamp.now()

    # ========================================================
    # CurveInput
    # ========================================================

    curve_input = (
        _read_market_input_for_date(
            CURVE_FILE,
            valuation_date,
        )
    )

    if not curve_input.empty:
        curve_input = curve_input.copy()
        curve_input["Source"] = (
            CURVE_FILE.name
        )

    curve_input.to_csv(
        CURVE_INPUT_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # CurveResult
    # ========================================================

    curve_result = simulation.get(
        "_curve_result_df"
    )

    if isinstance(
        curve_result,
        pd.DataFrame
    ):
        curve_result = (
            curve_result.copy()
        )
        curve_result["Source"] = (
            "Bootstrapping.py"
        )
    else:
        curve_result = pd.DataFrame()

    curve_result.to_csv(
        CURVE_RESULT_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # SwaptionInput
    # ========================================================

    swaption_input = (
        _read_market_input_for_date(
            SWAPTION_VOL_FILE,
            valuation_date,
        )
    )

    if not swaption_input.empty:
        swaption_input = (
            swaption_input.copy()
        )
        swaption_input["Source"] = (
            SWAPTION_VOL_FILE.name
        )

    swaption_input.to_csv(
        SWAPTION_INPUT_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # G2Calibration Summary
    # ========================================================

    summary = simulation.get(
        "_calibration_summary"
    )

    if summary is None:
        summary = (
            _calibration_summary_from_info(
                simulation.get(
                    "_calibration_info"
                ),
                model=simulation.get(
                    "model"
                ),
            )
        )

    summary_rows = [
        ["a", summary.get("a"), summary.get("initial_a"), "SwaptionVolCalib.py"],
        ["sigma", summary.get("sigma"), summary.get("initial_sigma"), "SwaptionVolCalib.py"],
        ["b", summary.get("b"), summary.get("initial_b"), "SwaptionVolCalib.py"],
        ["eta", summary.get("eta"), summary.get("initial_eta"), "SwaptionVolCalib.py"],
        ["rho", summary.get("rho"), summary.get("initial_rho"), "SwaptionVolCalib.py"],
        ["Engine", summary.get("engine"), CALIBRATION_ENGINE, "SwaptionVolCalib.py"],
        ["Tree Steps", summary.get("tree_steps"), TREE_STEPS, "SwaptionVolCalib.py"],
        ["Price MAE", summary.get("price_mae"), None, "SwaptionVolCalib.py"],
        ["Price RMSE", summary.get("price_rmse"), None, "SwaptionVolCalib.py"],
        ["Relative Price MAE", summary.get("relative_price_mae"), None, "SwaptionVolCalib.py"],
        ["Relative Price RMSE", summary.get("relative_price_rmse"), None, "SwaptionVolCalib.py"],
        ["Number of Helpers", summary.get("number_of_helpers"), None, "SwaptionVolCalib.py"],
        ["Missing Quotes", summary.get("number_of_missing_quotes"), None, "SwaptionVolCalib.py"],
        ["Fixed Leg Tenor", summary.get("fixed_leg_tenor"), FIXED_LEG_TENOR, "SwaptionVolCalib.py"],
        ["Floating Index Tenor", summary.get("floating_index_tenor"), FLOATING_INDEX_TENOR, "SwaptionVolCalib.py"],
    ]

    g2_summary_df = pd.DataFrame(
        summary_rows,
        columns=[
            "Parameter",
            "Value",
            "Initial Value",
            "Source",
        ],
    )

    g2_summary_df.to_csv(
        G2_SUMMARY_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # G2Calibration Detail
    # ========================================================

    g2_detail = simulation.get(
        "_calibration_result_df"
    )

    if isinstance(
        g2_detail,
        pd.DataFrame
    ):
        g2_detail = g2_detail.copy()
    else:
        g2_detail = pd.DataFrame()

    g2_detail.to_csv(
        G2_DETAIL_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # Simulation
    # ========================================================

    model = simulation.get(
        "model"
    )

    if model is not None:
        p = model.params()
        sim_params = {
            "a": float(p[0]),
            "sigma": float(p[1]),
            "b": float(p[2]),
            "eta": float(p[3]),
            "rho": float(p[4]),
        }
    else:
        sim_params = {
            k: summary.get(k)
            for k in (
                "a",
                "sigma",
                "b",
                "eta",
                "rho",
            )
        }

    x_array = np.asarray(
        simulation["x"]
    )

    simulation_rows = [
        ["Simulation Mode", SIMULATION_MODE.upper(), "FAST / FINAL", "RunCallableHybridBond.py"],
        ["Cache Used", bool(simulation.get("_cache_file")), "기존 MC simulation cache 사용 여부", "RunCallableHybridBond.py"],
        ["Cache File", simulation.get("_cache_file", ""), "사용한 cache 파일", "RunCallableHybridBond.py"],
        ["Valuation Date", ql_date_to_yyyymmdd(valuation_date), "평가기준일", "Valuation"],
        ["Number of Paths", int(x_array.shape[0]), "실제 생성/사용된 path 수", "G2Simulation.py"],
        ["Number of Time Steps", int(len(simulation["times"])), "실제 time grid 개수", "G2Simulation.py"],
        ["Simulation Horizon (Y)", SIMULATION_YEARS, "Simulation 기간", "RunCallableHybridBond.py"],
        ["Time Step (M)", TIME_STEP_MONTHS, "Simulation step", "RunCallableHybridBond.py"],
        ["Random Seed", RANDOM_SEED, "난수 seed", "RunCallableHybridBond.py"],
        ["a", sim_params.get("a"), "Calibrated G2 parameter", "G2Simulation.py"],
        ["sigma", sim_params.get("sigma"), "Calibrated G2 parameter", "G2Simulation.py"],
        ["b", sim_params.get("b"), "Calibrated G2 parameter", "G2Simulation.py"],
        ["eta", sim_params.get("eta"), "Calibrated G2 parameter", "G2Simulation.py"],
        ["rho", sim_params.get("rho"), "Calibrated G2 parameter", "G2Simulation.py"],
        ["Discount Factors Stored", "discount_factors" in simulation, "Pathwise cumulative discount factor", "G2Simulation.py"],
        ["Integrated Short Rates Stored", "integrated_rates" in simulation, "Pathwise integrated short rates", "G2Simulation.py"],
    ]

    pd.DataFrame(
        simulation_rows,
        columns=[
            "Item",
            "Value",
            "Description",
            "Source",
        ],
    ).to_csv(
        SIMULATION_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    # ========================================================
    # Exercise / CouponSchedule
    # ========================================================

    exercise_frames = []
    coupon_frames = []

    for bond_name, result in results.items():

        ex = result.get(
            "Exercise Statistics"
        )

        if (
            isinstance(ex, pd.DataFrame)
            and
            not ex.empty
        ):
            ex = ex.copy()
            ex.insert(
                0,
                "Bond Name",
                bond_name,
            )
            ex["Run Timestamp"] = (
                run_timestamp
            )
            exercise_frames.append(
                ex
            )

        cp = result.get(
            "Coupon Schedule"
        )

        if (
            isinstance(cp, pd.DataFrame)
            and
            not cp.empty
        ):
            cp = cp.copy()
            cp.insert(
                0,
                "Bond Name",
                bond_name,
            )
            cp["Run Timestamp"] = (
                run_timestamp
            )
            coupon_frames.append(
                cp
            )

    if exercise_frames:
        exercise_df = pd.concat(
            exercise_frames,
            ignore_index=True,
        )
    else:
        exercise_df = pd.DataFrame(
            columns=[
                "Bond Name",
                "Call Date",
                "Call Price",
                "Exercise Paths",
                "Exercise %",
                "Run Timestamp",
            ]
        )

    exercise_df.to_csv(
        EXERCISE_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    if coupon_frames:
        coupon_df = pd.concat(
            coupon_frames,
            ignore_index=True,
        )
    else:
        coupon_df = pd.DataFrame(
            columns=[
                "Bond Name",
                "Accrual Start",
                "Accrual End",
                "Payment Date",
                "Accrual Factor",
                "Coupon Rule",
                "Mean Coupon Rate",
                "Min Coupon Rate",
                "Max Coupon Rate",
                "Mean Coupon Amount",
                "Run Timestamp",
            ]
        )

    coupon_df.to_csv(
        COUPON_SCHEDULE_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    return {
        "CurveInput": CURVE_INPUT_CSV_FILE,
        "CurveResult": CURVE_RESULT_CSV_FILE,
        "SwaptionInput": SWAPTION_INPUT_CSV_FILE,
        "G2CalibrationSummary": G2_SUMMARY_CSV_FILE,
        "G2CalibrationDetail": G2_DETAIL_CSV_FILE,
        "Simulation": SIMULATION_CSV_FILE,
        "Exercise": EXERCISE_CSV_FILE,
        "CouponSchedule": COUPON_SCHEDULE_CSV_FILE,
    }

def export_results_csv(
    results: dict,
) -> Path:

    rows = []

    for (
        bond_name,
        result
    ) in results.items():

        exercise_df = result.get(
            "Exercise Statistics"
        )

        total_exercise_pct = 0.0

        if (
            isinstance(
                exercise_df,
                pd.DataFrame
            )
            and
            not exercise_df.empty
            and
            "Exercise %" in exercise_df.columns
        ):

            total_exercise_pct = float(
                pd.to_numeric(
                    exercise_df[
                        "Exercise %"
                    ],
                    errors="coerce",
                )
                .fillna(0.0)
                .sum()
            )

        rows.append(
            {
                "Bond Name":
                    bond_name,

                "Valuation Date":
                    str(
                        result.get(
                            "Valuation Date",
                            ""
                        )
                    ),

                "Issue Date":
                    str(
                        result.get(
                            "Issue Date",
                            ""
                        )
                    ),

                "Maturity":
                    str(
                        result.get(
                            "Maturity",
                            ""
                        )
                    ),

                "First Call / Reset Date":
                    str(
                        result.get(
                            "First Call / Reset Date",
                            ""
                        )
                    ),

                "Initial Coupon Rate":
                    result.get(
                        "Initial Coupon Rate"
                    ),

                "Reset Rate Mode":
                    result.get(
                        "Reset Rate Mode"
                    ),

                "Mean Post Reset Coupon Rate":
                    result.get(
                        "Mean Post Reset Coupon Rate"
                    ),

                "Notional":
                    result.get(
                        "Notional"
                    ),

                "Straight Bond Price":
                    result.get(
                        "Straight Bond Price"
                    ),

                "Callable Bond Price":
                    result.get(
                        "Callable Bond Price"
                    ),

                "Callable Option Value":
                    result.get(
                        "Callable Option Value"
                    ),

                "Standard Error":
                    result.get(
                        "Standard Error"
                    ),

                "95% CI Lower":
                    result.get(
                        "95% CI Lower"
                    ),

                "95% CI Upper":
                    result.get(
                        "95% CI Upper"
                    ),

                "Total Call Exercise %":
                    total_exercise_pct,
            }
        )

    output_df = pd.DataFrame(
        rows
    )

    output_df.to_csv(
        RESULT_CSV_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    return RESULT_CSV_FILE


# ============================================================
# 10. Main
# ============================================================

def main() -> int:

    # --------------------------------------------------------
    # VBA가 RunTool 폴더의 현재 Excel 경로를 첫 번째 command-line 인자로 전달
    # --------------------------------------------------------

    if len(
        sys.argv
    ) >= 2:

        bond_excel = Path(
            sys.argv[1]
        ).resolve()

    else:

        bond_excel = (
            DEFAULT_BOND_FILE
            .resolve()
        )

    print("=" * 80)
    print("Callable Hybrid Bond Batch Valuation")
    print("=" * 80)

    print(
        f"Project Root : {PROJECT_ROOT}"
    )

    print(
        f"RunTool Path : {RUN_TOOL_PATH}"
    )

    print(
        f"Function Path: {FUNCTION_PATH}"
    )

    print(
        f"Bond Excel   : {bond_excel}"
    )

    print(
        f"Curve Excel  : {CURVE_FILE}"
    )

    print(
        f"Swaption Vol : {SWAPTION_VOL_FILE}"
    )

    # --------------------------------------------------------
    # Input file validation
    # --------------------------------------------------------

    if not bond_excel.is_file():
        raise FileNotFoundError(
            "Callable Hybrid Bond 입력 Excel을 찾을 수 없습니다:\n"
            f"{bond_excel}"
        )

    if not CURVE_FILE.is_file():
        raise FileNotFoundError(
            "Yield Curve 입력 Excel을 찾을 수 없습니다:\n"
            f"{CURVE_FILE}"
        )

    if not SWAPTION_VOL_FILE.is_file():
        raise FileNotFoundError(
            "Swaption Vol 입력 Excel을 찾을 수 없습니다:\n"
            f"{SWAPTION_VOL_FILE}"
        )

    # --------------------------------------------------------
    # Valuation Date
    # --------------------------------------------------------

    valuation_date = (
        get_valuation_date_from_bond_excel(
            bond_excel
        )
    )

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    print()

    print(
        f"Valuation Date: "
        f"{valuation_date}"
    )

    print(
        f"Calibration Date: "
        f"{ql_date_to_yyyymmdd(valuation_date)}"
    )

    # --------------------------------------------------------
    # Curve -> Calibration -> Simulation
    # --------------------------------------------------------

    print(
        f"Simulation Mode: {SIMULATION_MODE.upper()} "
        f"({N_PATHS:,} paths)"
    )

    simulation = (
        get_or_build_g2_simulation(
            valuation_date
        )
    )

    # --------------------------------------------------------
    # Callable Hybrid Bond Batch Valuation
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("5. Callable Hybrid Bond Batch Pricing")
    print("=" * 80)

    results = (
        price_callable_hybrid_bond_batch(

            simulation=simulation,

            excel_path=
                str(
                    bond_excel
                ),

            sheet_name=
                "Valuation",
        )
    )

    # --------------------------------------------------------
    # Console output
    # --------------------------------------------------------

    print_batch_results(
        results
    )

    # --------------------------------------------------------
    # Detailed CSVs for Excel sheets
    # --------------------------------------------------------

    detail_files = export_detail_csvs(
        simulation=simulation,
        results=results,
        valuation_date=valuation_date,
    )

    # --------------------------------------------------------
    # Summary CSV
    # --------------------------------------------------------

    csv_file = (
        export_results_csv(
            results
        )
    )

    # --------------------------------------------------------
    # Done
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("Callable Hybrid Bond Valuation 완료")
    print("=" * 80)

    print(
        f"Result CSV    : "
        f"{csv_file}"
    )

    print(
        f"Number of Bonds: "
        f"{len(results)}"
    )

    print(
        "Detailed CSVs:"
    )

    for sheet_name, file_path in detail_files.items():
        print(
            f"  {sheet_name:<24}: {file_path}"
        )

    return 0


# ============================================================
# 11. Program Entry
# ============================================================

if __name__ == "__main__":

    try:

        exit_code = main()

        sys.exit(
            exit_code
        )

    except Exception as exc:

        print()
        print("=" * 80)
        print("ERROR")
        print("=" * 80)

        print(
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        print()

        traceback.print_exc()

        sys.exit(
            1
        )
