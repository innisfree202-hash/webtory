# ============================================================
# RunCallableBond.py
#
# Zero Coupon Callable Bond Excel Batch Valuation Runner
#
# - Up to 10 bonds at once from Valuation!B:K
# - Valuation!B2:K2 selects which bonds are valued (TRUE/FALSE)
# - Rows 5:11 are inputs for each bond
# - Market/model work is shared by valuation date
# - Results are written back to Valuation
# - Every run is appended to ValuationHistory
# - Call/exercise details are used internally for pricing but are not written to separate sheets
# ============================================================

import os
import sys
import traceback
from datetime import datetime

import QuantLib as ql
import numpy as np
import pandas as pd
import win32com.client


# ============================================================
# 1. Project Paths
# ============================================================

RUNTOOL_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(RUNTOOL_DIR)
FUNCTION_DIR = os.path.join(PROJECT_DIR, "Function")
INPUT_DIR = os.path.join(PROJECT_DIR, "Input")

if FUNCTION_DIR not in sys.path:
    sys.path.insert(0, FUNCTION_DIR)


# ============================================================
# 2. Project Modules
# ============================================================

from Bootstrapping import bootstrap_curve_from_excel
from SwaptionVolCalib import calibrate_g2
from G2Simulation import simulate_g2_short_rate
from CallDate import generate_call_dates

from CallableSwap import (
    price_zero_coupon_callable_bond,
    print_zero_coupon_callable_bond_result,
)


# ============================================================
# 3. Files / Excel Settings
# ============================================================

YIELD_CURVE_FILE = os.path.join(
    INPUT_DIR,
    "Yield_depositSwap.xlsx"
)

SWAPTION_VOL_FILE = os.path.join(
    INPUT_DIR,
    "swaption_vol_long_format.xlsx"
)

VALUATION_SHEET_NAME = "Valuation"
G2_SHEET_NAME = "G2Calibration"
SIMULATION_SHEET_NAME = "Simulation"
HISTORY_SHEET_NAME = "ValuationHistory"

EXPECTED_WORKBOOK_NAMES = [
    "Callable_Bond_Valuation.xlsm",
    "Callable_Bond_Valuation_Template.xlsm",
    "Callable_Bond_Valuation_Batch_Final.xlsm",
        "Callable_Bond_Valuation_Batch_Final_v2.xlsm",
        "Callable_Bond_Valuation_Batch.xlsm",
]

# B:K = maximum 10 instruments
FIRST_INPUT_COLUMN = 2
LAST_INPUT_COLUMN = 11

ROW_SELECTED = 2
ROW_VALUATION_DATE = 5
ROW_TRADE_DATE = 6
ROW_MATURITY = 7
ROW_CALL_FREQUENCY = 8
ROW_CALL_EXISTS = 9
ROW_ZERO_COUPON_RATE = 10
ROW_NOTIONAL = 11


# ============================================================
# 4. Monte Carlo Settings
# ============================================================

N_PATHS = 10_000
TIME_STEP_MONTHS = 1
RANDOM_SEED = 12345


# ============================================================
# 5. Basic Helpers
# ============================================================

def _excel_date_to_ql_date(value):

    if value is None or value == "":
        raise ValueError("Excel 날짜가 비어 있습니다.")

    if isinstance(value, ql.Date):
        return value

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

    # Excel COM normally returns datetime, but this also handles text.
    timestamp = pd.to_datetime(
        str(value).strip()
    )

    return ql.Date(
        int(timestamp.day),
        int(timestamp.month),
        int(timestamp.year)
    )


def _excel_to_bool(value):

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().upper()

    if text in ("TRUE", "T", "YES", "Y", "1"):
        return True

    if text in ("FALSE", "F", "NO", "N", "0"):
        return False

    raise ValueError(
        f"TRUE/FALSE 값을 인식할 수 없습니다: {value}"
    )


def _ql_date_to_text(date):

    return (
        f"{date.year():04d}-"
        f"{int(date.month()):02d}-"
        f"{date.dayOfMonth():02d}"
    )


def _ql_date_to_yyyymmdd(date):

    return (
        f"{date.year():04d}"
        f"{int(date.month()):02d}"
        f"{date.dayOfMonth():02d}"
    )


def _yyyymmdd_to_ql_date(value):

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    if len(text) != 8 or not text.isdigit():
        raise ValueError(
            f"YYYYMMDD 형식이 아닙니다: {value}"
        )

    return ql.Date(
        int(text[6:8]),
        int(text[4:6]),
        int(text[0:4])
    )


def _normalize_market_date(value):

    if pd.isna(value):
        return None

    if (
        hasattr(value, "year")
        and hasattr(value, "month")
        and hasattr(value, "day")
    ):
        return (
            f"{int(value.year):04d}"
            f"{int(value.month):02d}"
            f"{int(value.day):02d}"
        )

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    if len(text) == 8 and text.isdigit():
        return text

    timestamp = pd.to_datetime(text)

    return (
        f"{timestamp.year:04d}"
        f"{timestamp.month:02d}"
        f"{timestamp.day:02d}"
    )


def _tenor_to_years(value):

    text = str(value).strip().upper()

    if text.endswith("Y"):
        return float(text[:-1])

    if text.endswith("M"):
        return float(text[:-1]) / 12.0

    if text.endswith("W"):
        return float(text[:-1]) / 52.0

    if text.endswith("D"):
        return float(text[:-1]) / 365.0

    raise ValueError(
        f"지원하지 않는 Tenor 형식입니다: {value}"
    )


def _column_letter(column_number):

    result = ""
    n = int(column_number)

    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result

    return result


# ============================================================
# 6. Excel Input - up to 10 bonds
# ============================================================

def _is_empty(value):

    return value is None or str(value).strip() == ""


def _read_excel_inputs_for_column(sheet, column_number):

    col = _column_letter(column_number)

    values = {
        "valuation_date": sheet.Cells(
            ROW_VALUATION_DATE,
            column_number
        ).Value,
        "trade_date": sheet.Cells(
            ROW_TRADE_DATE,
            column_number
        ).Value,
        "maturity": sheet.Cells(
            ROW_MATURITY,
            column_number
        ).Value,
        "call_frequency": sheet.Cells(
            ROW_CALL_FREQUENCY,
            column_number
        ).Value,
        "call_exists": sheet.Cells(
            ROW_CALL_EXISTS,
            column_number
        ).Value,
        "zero_coupon_rate": sheet.Cells(
            ROW_ZERO_COUPON_RATE,
            column_number
        ).Value,
        "notional": sheet.Cells(
            ROW_NOTIONAL,
            column_number
        ).Value,
    }

    # A completely blank column is simply ignored.
    if all(_is_empty(v) for v in values.values()):
        return None

    # If the column is used, all required cells must be populated.
    missing = [
        key
        for key, value in values.items()
        if _is_empty(value)
    ]

    if missing:
        raise ValueError(
            f"{col}열 입력값이 불완전합니다. "
            f"누락 항목: {', '.join(missing)}"
        )

    valuation_date = _excel_date_to_ql_date(
        values["valuation_date"]
    )

    trade_date = _excel_date_to_ql_date(
        values["trade_date"]
    )

    maturity = _excel_date_to_ql_date(
        values["maturity"]
    )

    call_frequency = str(
        values["call_frequency"]
    ).strip().upper()

    call_exists = _excel_to_bool(
        values["call_exists"]
    )

    zero_coupon_rate = float(
        values["zero_coupon_rate"]
    )

    notional = float(
        values["notional"]
    )

    if trade_date > valuation_date:
        raise ValueError(
            f"{col}열: Trade Date가 Valuation Date보다 미래입니다."
        )

    if maturity <= valuation_date:
        raise ValueError(
            f"{col}열: Maturity는 Valuation Date보다 이후여야 합니다."
        )

    if notional <= 0.0:
        raise ValueError(
            f"{col}열: Notional은 0보다 커야 합니다."
        )

    if zero_coupon_rate <= -1.0:
        raise ValueError(
            f"{col}열: Zero Coupon Rate는 -100%보다 커야 합니다."
        )

    return {
        "bond_no": column_number - FIRST_INPUT_COLUMN + 1,
        "input_column": col,
        "valuation_date": valuation_date,
        "trade_date": trade_date,
        "maturity": maturity,
        "call_frequency": call_frequency,
        "call_exists": call_exists,
        "zero_coupon_rate": zero_coupon_rate,
        "notional": notional,
    }


def _read_all_excel_inputs(sheet):

    items = []
    selected_columns = []

    for column_number in range(
        FIRST_INPUT_COLUMN,
        LAST_INPUT_COLUMN + 1
    ):

        col = _column_letter(
            column_number
        )

        selected_raw = sheet.Cells(
            ROW_SELECTED,
            column_number
        ).Value

        # Blank selection is treated as FALSE.
        if _is_empty(selected_raw):
            selected = False
        else:
            selected = _excel_to_bool(
                selected_raw
            )

        if not selected:
            continue

        selected_columns.append(col)

        item = _read_excel_inputs_for_column(
            sheet,
            column_number
        )

        if item is None:
            raise ValueError(
                f"{col}열은 Valuation 2행에서 평가 대상으로 "
                "선택되었지만 B5:K11 입력값이 비어 있습니다."
            )

        item["selected"] = True
        items.append(item)

    if not items:
        raise ValueError(
            "평가 대상으로 선택된 Bond가 없습니다.\n\n"
            "Valuation 시트의 B2:K2 중 평가할 Bond를 TRUE로 "
            "선택하세요."
        )

    print(
        "Selected Columns    :",
        ", ".join(selected_columns)
    )

    return items


# ============================================================
# 7. Market Data
# ============================================================

def _validate_input_files():

    for file_path in (
        YIELD_CURVE_FILE,
        SWAPTION_VOL_FILE
    ):

        if not os.path.isfile(file_path):
            raise FileNotFoundError(
                f"Input 파일을 찾을 수 없습니다:\n{file_path}"
            )


def _read_market_dates(file_path):

    data = pd.read_excel(file_path)

    if "Data" not in data.columns:
        raise ValueError(
            f"{os.path.basename(file_path)}에 "
            "'Data' 컬럼이 없습니다."
        )

    dates = []

    for value in data["Data"]:
        normalized = _normalize_market_date(value)

        if normalized is not None:
            dates.append(normalized)

    return sorted(set(dates))


def _select_market_data_date(valuation_date):

    valuation_key = _ql_date_to_yyyymmdd(
        valuation_date
    )

    yield_dates = _read_market_dates(
        YIELD_CURVE_FILE
    )

    vol_dates = _read_market_dates(
        SWAPTION_VOL_FILE
    )

    common_dates = sorted(
        set(yield_dates).intersection(vol_dates)
    )

    eligible = [
        d
        for d in common_dates
        if d <= valuation_key
    ]

    if not eligible:
        raise ValueError(
            "Valuation Date 이하의 공통 Market Data Date가 없습니다."
        )

    return eligible[-1]


def _load_swaption_vol_matrix(
    file_path,
    market_data_date
):

    data = pd.read_excel(file_path)

    if "Data" not in data.columns:
        raise ValueError(
            "Swaption Vol 파일에 'Data' 컬럼이 없습니다."
        )

    data = data.copy()

    data["_MarketDate"] = data[
        "Data"
    ].map(_normalize_market_date)

    data = data[
        data["_MarketDate"] == market_data_date
    ].copy()

    if data.empty:
        raise ValueError(
            "선택된 Market Data Date의 "
            "Swaption Vol 데이터가 없습니다."
        )

    normalized = {
        str(c).strip().lower(): c
        for c in data.columns
    }

    expiry_column = None
    tenor_column = None
    vol_column = None

    for candidate in (
        "옵션만기",
        "expiry",
        "option expiry",
        "option tenor",
        "optiontenor"
    ):

        if candidate.lower() in normalized:
            expiry_column = normalized[
                candidate.lower()
            ]
            break

    for candidate in (
        "기초자산만기",
        "swap tenor",
        "swaptenor",
        "underlying tenor",
        "tenor"
    ):

        if candidate.lower() in normalized:
            tenor_column = normalized[
                candidate.lower()
            ]
            break

    for candidate in (
        "변동성",
        "vol",
        "volatility",
        "swaption vol"
    ):

        if candidate.lower() in normalized:
            vol_column = normalized[
                candidate.lower()
            ]
            break

    if (
        expiry_column is None
        or tenor_column is None
        or vol_column is None
    ):
        raise ValueError(
            "Swaption Vol 컬럼을 인식하지 못했습니다."
        )

    data[expiry_column] = (
        data[expiry_column]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    data[tenor_column] = (
        data[tenor_column]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    data[vol_column] = pd.to_numeric(
        data[vol_column],
        errors="raise"
    )

    swaption_vols = data.pivot(
        index=expiry_column,
        columns=tenor_column,
        values=vol_column
    )

    row_order = sorted(
        swaption_vols.index,
        key=_tenor_to_years
    )

    column_order = sorted(
        swaption_vols.columns,
        key=_tenor_to_years
    )

    # Sparse matrix is intentionally allowed.
    # Missing Expiry/Tenor quotes remain NaN and are skipped
    # by the optimized SwaptionVolCalib.py.
    return swaption_vols.loc[
        row_order,
        column_order
    ]


# ============================================================
# 8. Excel Utilities
# ============================================================

def _get_workbook(excel):

    workbook = excel.ActiveWorkbook

    if workbook is not None:
        return workbook

    for i in range(
        1,
        excel.Workbooks.Count + 1
    ):

        candidate = excel.Workbooks.Item(i)

        if str(candidate.Name).lower() in [
            name.lower()
            for name in EXPECTED_WORKBOOK_NAMES
        ]:
            return candidate

    raise RuntimeError(
        "Callable Bond 평가용 Excel Workbook을 찾지 못했습니다."
    )


def _get_or_create_sheet(
    workbook,
    sheet_name
):

    try:
        return workbook.Worksheets(sheet_name)

    except Exception:

        sheet = workbook.Worksheets.Add(
            After=workbook.Worksheets(
                workbook.Worksheets.Count
            )
        )

        sheet.Name = sheet_name
        return sheet


def _clear_sheet_values(sheet):

    try:
        sheet.UsedRange.ClearContents()

    except Exception:
        pass


def _python_scalar(value):

    if isinstance(value, ql.Date):
        return _ql_date_to_text(value)

    if isinstance(value, np.generic):
        return value.item()

    if value is None:
        return ""

    return value


def _write_matrix(
    sheet,
    start_row,
    start_col,
    rows
):

    if not rows:
        return

    width = max(
        len(row)
        for row in rows
    )

    normalized = []

    for row in rows:

        converted = []

        for value in list(row) + [""] * (
            width - len(row)
        ):

            value = _python_scalar(value)

            try:
                if pd.isna(value):
                    value = ""

            except Exception:
                pass

            converted.append(value)

        normalized.append(
            tuple(converted)
        )

    end_row = (
        start_row
        + len(normalized)
        - 1
    )

    end_col = (
        start_col
        + width
        - 1
    )

    sheet.Range(
        sheet.Cells(
            start_row,
            start_col
        ),
        sheet.Cells(
            end_row,
            end_col
        )
    ).Value = tuple(normalized)


def _autofit(sheet, max_width=28):

    try:

        sheet.UsedRange.Columns.AutoFit()

        count = int(
            sheet.UsedRange.Columns.Count
        )

        for j in range(
            1,
            count + 1
        ):

            col = sheet.Columns(j)

            if col.ColumnWidth > max_width:
                col.ColumnWidth = max_width

    except Exception:
        pass


# ============================================================
# 9. Batch Output - Valuation
# ============================================================

def _never_exercised_stats(result):

    exercise_date_index = np.asarray(
        result["Exercise Date Index"],
        dtype=int
    )

    n_paths = len(
        exercise_date_index
    )

    never_paths = int(
        np.sum(
            exercise_date_index < 0
        )
    )

    never_pct = (
        never_paths
        / n_paths
        * 100.0
        if n_paths > 0
        else 0.0
    )

    return (
        never_paths,
        never_pct
    )


def _write_batch_valuation_sheet(
    sheet,
    valuation_records
):

    # Preserve A1:K11 inputs; clear only result area.
    sheet.Range(
        "A14:K80"
    ).ClearContents()

    result_by_bond = {
        record["inputs"]["bond_no"]: record
        for record in valuation_records
    }

    header = [
        "Metric"
    ] + [
        f"Bond {i}"
        for i in range(1, 11)
    ]

    metrics = [
        "Status",
        "Input Column",
        "Valuation Date",
        "Market Data Date",
        "Trade Date",
        "Maturity",
        "Call Exists",
        "Call Frequency",
        "Zero Coupon Rate",
        "Notional",
        "Maturity Value",
        "Straight Bond Price",
        "Callable Bond Price",
        "Callable Option Value",
        "Standard Error",
        "95% CI Lower",
        "95% CI Upper",
        "Never Exercised Paths",
        "Never Exercised %",
        "Number of Call Dates",
        "Number of Paths",
    ]

    rows = [
        ["CURRENT RUN RESULTS"],
        header
    ]

    for metric in metrics:

        row = [metric]

        for bond_no in range(1, 11):

            record = result_by_bond.get(
                bond_no
            )

            if record is None:
                row.append("")
                continue

            inputs = record["inputs"]
            result = record["result"]
            simulation = record["simulation"]
            market_data_date = record[
                "market_data_date"
            ]

            never_paths, never_pct = (
                _never_exercised_stats(
                    result
                )
            )

            value_map = {
                "Status":
                    "OK",
                "Input Column":
                    inputs["input_column"],
                "Valuation Date":
                    _ql_date_to_text(
                        inputs["valuation_date"]
                    ),
                "Market Data Date":
                    market_data_date,
                "Trade Date":
                    _ql_date_to_text(
                        inputs["trade_date"]
                    ),
                "Maturity":
                    _ql_date_to_text(
                        inputs["maturity"]
                    ),
                "Call Exists":
                    inputs["call_exists"],
                "Call Frequency":
                    inputs["call_frequency"],
                "Zero Coupon Rate":
                    result["Zero Coupon Rate"],
                "Notional":
                    result["Notional"],
                "Maturity Value":
                    result["Maturity Value"],
                "Straight Bond Price":
                    result["Straight Bond Price"],
                "Callable Bond Price":
                    result["Callable Bond Price"],
                "Callable Option Value":
                    result["Callable Option Value"],
                "Standard Error":
                    result["Standard Error"],
                "95% CI Lower":
                    result["95% CI Lower"],
                "95% CI Upper":
                    result["95% CI Upper"],
                "Never Exercised Paths":
                    never_paths,
                "Never Exercised %":
                    never_pct,
                "Number of Call Dates":
                    len(
                        record["call_dates"]
                    ),
                "Number of Paths":
                    simulation["n_paths"],
            }

            row.append(
                value_map[metric]
            )

        rows.append(row)

    _write_matrix(
        sheet,
        14,
        1,
        rows
    )

    try:
        sheet.Range("B24:K24").NumberFormat = "0.0000%"
        sheet.Range("B23:K29").NumberFormat = (
            '#,##0.000000;[Red](#,##0.000000);-'
        )
        sheet.Range("B34:K34").NumberFormat = "0.00"
    except Exception:
        pass

    _autofit(
        sheet,
        max_width=24
    )


# ============================================================
# 10. Persistent History
# ============================================================

HISTORY_HEADERS = [
    "Run ID",
    "Run Timestamp",
    "Bond No",
    "Input Column",
    "Valuation Date",
    "Market Data Date",
    "Trade Date",
    "Maturity",
    "Call Exists",
    "Call Frequency",
    "Zero Coupon Rate",
    "Notional",
    "Maturity Value",
    "Straight Bond Price",
    "Callable Bond Price",
    "Callable Option Value",
    "Standard Error",
    "95% CI Lower",
    "95% CI Upper",
    "Never Exercised Paths",
    "Never Exercised %",
    "Number of Call Dates",
    "Number of Paths",
    "Time Step",
    "G2 a",
    "G2 sigma",
    "G2 b",
    "G2 eta",
    "G2 rho",
]


def _append_history(
    sheet,
    valuation_records,
    run_id,
    run_timestamp
):

    if (
        sheet.Cells(1, 1).Value is None
        or str(
            sheet.Cells(1, 1).Value
        ).strip() == ""
    ):

        _write_matrix(
            sheet,
            1,
            1,
            [HISTORY_HEADERS]
        )

        start_row = 2

    else:

        start_row = (
            sheet.Cells(
                sheet.Rows.Count,
                1
            )
            .End(-4162)  # xlUp
            .Row
            + 1
        )

    rows = []

    for record in valuation_records:

        inputs = record["inputs"]
        result = record["result"]
        simulation = record["simulation"]
        calibration_info = record[
            "calibration_info"
        ]

        never_paths, never_pct = (
            _never_exercised_stats(
                result
            )
        )

        params = calibration_info.get(
            "parameters",
            {}
        )

        rows.append([
            run_id,
            run_timestamp,
            inputs["bond_no"],
            inputs["input_column"],
            _ql_date_to_text(
                inputs["valuation_date"]
            ),
            record["market_data_date"],
            _ql_date_to_text(
                inputs["trade_date"]
            ),
            _ql_date_to_text(
                inputs["maturity"]
            ),
            inputs["call_exists"],
            inputs["call_frequency"],
            result["Zero Coupon Rate"],
            result["Notional"],
            result["Maturity Value"],
            result["Straight Bond Price"],
            result["Callable Bond Price"],
            result["Callable Option Value"],
            result["Standard Error"],
            result["95% CI Lower"],
            result["95% CI Upper"],
            never_paths,
            never_pct,
            len(
                record["call_dates"]
            ),
            simulation["n_paths"],
            simulation["dt"],
            params.get("a", ""),
            params.get("sigma", ""),
            params.get("b", ""),
            params.get("eta", ""),
            params.get("rho", ""),
        ])

    _write_matrix(
        sheet,
        start_row,
        1,
        rows
    )

    try:
        sheet.Rows(1).Font.Bold = True
        sheet.Range(
            f"K2:K{start_row + len(rows) - 1}"
        ).NumberFormat = "0.0000%"
    except Exception:
        pass

    _autofit(
        sheet,
        max_width=24
    )


# ============================================================
# 11. Current Run G2 / Simulation / Call / Exercise
# ============================================================

def _write_g2_sheet(
    sheet,
    group_outputs
):

    _clear_sheet_values(sheet)

    row = 1

    for group_no, group in enumerate(
        group_outputs,
        start=1
    ):

        info = group["calibration_info"]
        result_df = group[
            "calibration_result"
        ]
        params = info.get(
            "parameters",
            {}
        )

        rows = [
            (
                f"G2++ CALIBRATION GROUP {group_no}",
                ""
            ),
            (
                "Market Data Date",
                group["market_data_date"]
            ),
            ("a", params.get("a", "")),
            ("sigma", params.get("sigma", "")),
            ("b", params.get("b", "")),
            ("eta", params.get("eta", "")),
            ("rho", params.get("rho", "")),
            (
                "Calibration Price MAE",
                info.get("price_mae", "")
            ),
            (
                "Calibration Price RMSE",
                info.get("price_rmse", "")
            ),
            (
                "Number of Helpers",
                info.get(
                    "number_of_helpers",
                    ""
                )
            ),
        ]

        _write_matrix(
            sheet,
            row,
            1,
            rows
        )

        row += len(rows) + 1

        if isinstance(
            result_df,
            pd.DataFrame
        ):

            detail = [
                tuple(
                    str(c)
                    for c in result_df.columns
                )
            ]

            detail.extend(
                result_df.itertuples(
                    index=False,
                    name=None
                )
            )

            _write_matrix(
                sheet,
                row,
                1,
                detail
            )

            row += len(detail) + 3

    _autofit(
        sheet,
        max_width=24
    )


def _write_simulation_sheet(
    sheet,
    group_outputs
):

    _clear_sheet_values(sheet)

    row = 1

    for group_no, group in enumerate(
        group_outputs,
        start=1
    ):

        simulation = group["simulation"]

        times = np.asarray(
            simulation["times"],
            dtype=float
        )

        dfs = np.asarray(
            simulation["discount_factors"],
            dtype=float
        )

        curve = simulation["curve"]
        n_paths = dfs.shape[0]

        mc_df = np.mean(
            dfs,
            axis=0
        )

        if n_paths > 1:

            mc_df_se = (
                np.std(
                    dfs,
                    axis=0,
                    ddof=1
                )
                / np.sqrt(n_paths)
            )

        else:

            mc_df_se = np.zeros_like(
                mc_df
            )

        market_df = np.array(
            [
                float(
                    curve.discount(
                        float(t)
                    )
                )
                for t in times
            ]
        )

        market_zero = np.full_like(
            times,
            np.nan
        )

        mc_zero = np.full_like(
            times,
            np.nan
        )

        positive = (
            times > 1.0e-12
        )

        market_zero[positive] = (
            -np.log(
                np.maximum(
                    market_df[positive],
                    1.0e-300
                )
            )
            / times[positive]
        )

        mc_zero[positive] = (
            -np.log(
                np.maximum(
                    mc_df[positive],
                    1.0e-300
                )
            )
            / times[positive]
        )

        rows = [
            [
                f"SIMULATION GROUP {group_no}",
                group["market_data_date"],
                "",
                "",
                "",
                "",
                "",
                ""
            ],
            [
                "Time (Years)",
                "Market DF",
                "MC DF",
                "MC DF SE",
                "DF Error",
                "Market Zero Rate",
                "MC Zero Rate",
                "Zero Rate Error"
            ]
        ]

        for i in range(
            len(times)
        ):

            rows.append([
                float(times[i]),
                float(market_df[i]),
                float(mc_df[i]),
                float(mc_df_se[i]),
                float(
                    mc_df[i]
                    - market_df[i]
                ),
                (
                    ""
                    if not positive[i]
                    else float(
                        market_zero[i]
                    )
                ),
                (
                    ""
                    if not positive[i]
                    else float(
                        mc_zero[i]
                    )
                ),
                (
                    ""
                    if not positive[i]
                    else float(
                        mc_zero[i]
                        - market_zero[i]
                    )
                ),
            ])

        _write_matrix(
            sheet,
            row,
            1,
            rows
        )

        row += len(rows) + 3

    _autofit(
        sheet,
        max_width=22
    )


# ============================================================
# 12. Market/Model Group Build
# ============================================================

def _build_market_model_group(
    valuation_date,
    group_inputs
):

    market_data_date = (
        _select_market_data_date(
            valuation_date
        )
    )

    valuation_key = (
        _ql_date_to_yyyymmdd(
            valuation_date
        )
    )

    # Current project convention: curve/model reference date
    # and valuation date must match exactly.
    if valuation_key != market_data_date:

        raise ValueError(
            "\n현재 Runner는 Market Data Date와 "
            "Valuation Date가 동일해야 합니다.\n\n"
            f"Valuation Date   = {valuation_key}\n"
            f"Market Data Date = {market_data_date}\n\n"
            "해당 Bond의 Valuation Date를 사용 가능한 "
            "Market Data Date와 동일하게 설정하세요."
        )

    calibration_ql_date = (
        _yyyymmdd_to_ql_date(
            market_data_date
        )
    )

    ql.Settings.instance().evaluationDate = (
        calibration_ql_date
    )

    print()
    print(
        f"[{valuation_key}] 1. Yield Curve Bootstrap"
    )

    curve, curve_result = (
        bootstrap_curve_from_excel(
            file_path=
                YIELD_CURVE_FILE,
            calibration_date=
                market_data_date,
            swap_index_tenor=
                "6M",
            fixed_leg_tenor=
                "1Y"
        )
    )

    print(
        f"[{valuation_key}] 2. Swaption Volatility"
    )

    swaption_vols = (
        _load_swaption_vol_matrix(
            SWAPTION_VOL_FILE,
            market_data_date
        )
    )

    print(
        f"[{valuation_key}] 3. G2++ Calibration"
    )

    (
        model,
        calibration_result,
        calibration_info
    ) = calibrate_g2(
        curve=curve,
        swaption_vols=swaption_vols,
        today=calibration_ql_date,
        fixed_leg_tenor="1Y",
        initial_params=(
            0.10,
            0.01,
            0.30,
            0.015,
            -0.70
        ),
        engine_range=6.0,
        engine_intervals=16,
        calculate_implied_vol=False
    )

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )

    day_counter = (
        ql.Actual365Fixed()
    )

    max_maturity_years = max(
        day_counter.yearFraction(
            valuation_date,
            item["maturity"]
        )
        for item in group_inputs
    )

    simulation_years = (
        int(
            np.ceil(
                max_maturity_years
            )
        )
        + 1
    )

    print(
        f"[{valuation_key}] 4. Exact G2++ Monte Carlo "
        f"(shared by {len(group_inputs)} bond(s))"
    )

    simulation = (
        simulate_g2_short_rate(
            curve=curve,
            model=model,
            simulation_years=
                simulation_years,
            n_paths=N_PATHS,
            time_step_months=
                TIME_STEP_MONTHS,
            seed=RANDOM_SEED,
            store_factors=True,
            store_integrated_rates=False
        )
    )

    return {
        "market_data_date":
            market_data_date,
        "curve":
            curve,
        "curve_result":
            curve_result,
        "model":
            model,
        "calibration_result":
            calibration_result,
        "calibration_info":
            calibration_info,
        "simulation":
            simulation,
    }


# ============================================================
# 13. Main
# ============================================================

def main():

    print()
    print("=" * 100)
    print(
        "Zero Coupon Callable Bond Batch Valuation "
        "(up to 10 bonds)"
    )
    print("=" * 100)

    excel = (
        win32com.client
        .GetActiveObject(
            "Excel.Application"
        )
    )

    workbook = _get_workbook(
        excel
    )

    valuation_sheet = (
        workbook.Worksheets(
            VALUATION_SHEET_NAME
        )
    )

    all_inputs = _read_all_excel_inputs(
        valuation_sheet
    )

    print()
    print(
        "Workbook          :",
        workbook.Name
    )
    print(
        "Number of Bonds   :",
        len(all_inputs)
    )

    _validate_input_files()

    # --------------------------------------------------------
    # Group by valuation date.
    # Curve + G2 calibration + MC simulation are reused inside
    # each group. This is the key batch-speed optimization.
    # --------------------------------------------------------

    grouped = {}

    for item in all_inputs:

        key = _ql_date_to_yyyymmdd(
            item["valuation_date"]
        )

        grouped.setdefault(
            key,
            []
        ).append(item)

    valuation_records = []
    group_outputs = []

    for group_key, group_inputs in sorted(
        grouped.items()
    ):

        valuation_date = group_inputs[
            0
        ]["valuation_date"]

        group = _build_market_model_group(
            valuation_date,
            group_inputs
        )

        group_outputs.append(group)

        simulation = group[
            "simulation"
        ]

        for inputs in group_inputs:

            print()
            print(
                "-" * 100
            )
            print(
                f"Bond {inputs['bond_no']} "
                f"({inputs['input_column']}열) 평가"
            )
            print(
                "-" * 100
            )

            trade_date = inputs[
                "trade_date"
            ]

            maturity = inputs[
                "maturity"
            ]

            valuation_date = inputs[
                "valuation_date"
            ]

            if inputs["call_exists"]:

                call_dates = generate_call_dates(
                    trade_date,
                    maturity,
                    valuation_date,
                    inputs["call_frequency"]
                )

            else:

                call_dates = []

            result = (
                price_zero_coupon_callable_bond(
                    simulation,
                    valuation_date,
                    trade_date,
                    maturity,
                    call_dates,
                    zero_coupon_rate=
                        inputs[
                            "zero_coupon_rate"
                        ],
                    notional=
                        inputs["notional"]
                )
            )

            print_zero_coupon_callable_bond_result(
                result
            )

            valuation_records.append({
                "inputs":
                    inputs,
                "market_data_date":
                    group[
                        "market_data_date"
                    ],
                "calibration_info":
                    group[
                        "calibration_info"
                    ],
                "simulation":
                    simulation,
                "call_dates":
                    call_dates,
                "result":
                    result,
            })

    # Keep Bond 1..10 output order.
    valuation_records = sorted(
        valuation_records,
        key=lambda x:
            x["inputs"]["bond_no"]
    )

    print()
    print("Write Current Run Results to Excel")

    g2_sheet = _get_or_create_sheet(
        workbook,
        G2_SHEET_NAME
    )

    simulation_sheet = _get_or_create_sheet(
        workbook,
        SIMULATION_SHEET_NAME
    )

    history_sheet = _get_or_create_sheet(
        workbook,
        HISTORY_SHEET_NAME
    )

    _write_batch_valuation_sheet(
        valuation_sheet,
        valuation_records
    )

    _write_g2_sheet(
        g2_sheet,
        group_outputs
    )

    _write_simulation_sheet(
        simulation_sheet,
        group_outputs
    )

    run_timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    run_id = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    _append_history(
        history_sheet,
        valuation_records,
        run_id,
        run_timestamp
    )

    workbook.Save()

    print()
    print("=" * 100)
    print(
        f"Batch Valuation Completed: "
        f"{len(valuation_records)} bond(s)"
    )
    print(
        f"Run ID: {run_id}"
    )
    print("=" * 100)


# ============================================================
# 14. Execute
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print()
        print("=" * 100)
        print("ERROR")
        print("=" * 100)
        print()
        print(str(error))
        print()

        traceback.print_exc()

        try:

            excel = (
                win32com.client
                .GetActiveObject(
                    "Excel.Application"
                )
            )

            workbook = _get_workbook(
                excel
            )

            sheet = (
                workbook.Worksheets(
                    VALUATION_SHEET_NAME
                )
            )

            _write_error(
                sheet,
                error
            )

            workbook.Save()

        except Exception:
            pass

        raise
