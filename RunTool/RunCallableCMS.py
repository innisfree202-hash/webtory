# ============================================================
# RunCallableCMS.py
#
# Callable CMS Swap Excel Valuation Runner
#
# Excel
#   ↓
# Market Data Date Selection
#   ↓
# Yield Curve Bootstrap
#   ↓
# Swaption Vol Matrix
#   ↓
# G2++ Calibration
#   ↓
# Exact G2++ Monte Carlo
#   ↓
# Call Date Generation
#   ↓
# Callable CMS Valuation
#   ↓
# Excel Output
#
# ============================================================


import os
import sys
import traceback

import QuantLib as ql
import numpy as np
import pandas as pd
import win32com.client


# ============================================================
# 1. Project Paths
# ============================================================

RUNTOOL_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

PROJECT_DIR = os.path.dirname(
    RUNTOOL_DIR
)

FUNCTION_DIR = os.path.join(
    PROJECT_DIR,
    "Function"
)

INPUT_DIR = os.path.join(
    PROJECT_DIR,
    "Input"
)


if FUNCTION_DIR not in sys.path:

    sys.path.insert(
        0,
        FUNCTION_DIR
    )


# ============================================================
# 2. Project Modules
# ============================================================

from Bootstrapping import (
    bootstrap_curve_from_excel
)

from SwaptionVolCalib import (
    calibrate_g2
)

from G2Simulation import (
    simulate_g2_short_rate
)

from CallDate import (
    generate_call_dates
)

from CallableCMS import (
    price_callable_cms_swap,
    print_callable_cms_swap_result
)


# ============================================================
# 3. Input Files
# ============================================================

YIELD_CURVE_FILE = os.path.join(
    INPUT_DIR,
    "Yield_depositSwap.xlsx"
)

SWAPTION_VOL_FILE = os.path.join(
    INPUT_DIR,
    "swaption_vol_long_format.xlsx"
)


# ============================================================
# 4. Excel Settings
# ============================================================

VALUATION_SHEET_NAME = "Valuation"

EXPECTED_WORKBOOK_NAME = (
    "Callable_CMS_Swap_Valuation.xlsm"
)


# ============================================================
# 5. Excel Input Cells
# ============================================================

CELL_VALUATION_DATE = "B5"
CELL_TRADE_DATE = "B6"
CELL_MATURITY = "B7"

CELL_CALL_FREQUENCY = "B8"
CELL_CALL_EXISTS = "B9"

CELL_FIXED_RATE = "B10"
CELL_FLOATING_SPREAD = "B11"

CELL_NOTIONAL = "B12"

CELL_FLOATING_TENOR = "B13"

CELL_RECEIVER_PAYER = "B14"
CELL_CALL_RIGHT = "B15"


# ============================================================
# 6. Monte Carlo Settings
# ============================================================

N_PATHS = 10_000

TIME_STEP_MONTHS = 1

RANDOM_SEED = 12345


# ============================================================
# 7. Output Settings
# ============================================================

OUTPUT_START_ROW = 18

EXERCISE_START_COLUMN = 4
# D

CALL_DATE_COLUMN = 8
# H


# ============================================================
# 8. Excel Date -> QuantLib Date
# ============================================================

def _excel_date_to_ql_date(value):

    if value is None:

        raise ValueError(
            "Excel 날짜가 비어 있습니다."
        )


    if isinstance(
        value,
        ql.Date
    ):

        return value


    # --------------------------------------------------------
    # Excel COM normally returns datetime
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # String fallback
    # --------------------------------------------------------

    text = str(value).strip()


    try:

        timestamp = pd.to_datetime(
            text
        )

        return ql.Date(

            int(timestamp.day),

            int(timestamp.month),

            int(timestamp.year)

        )

    except Exception as exc:

        raise ValueError(

            "Excel 날짜를 QuantLib Date로 "
            f"변환할 수 없습니다: {value}"

        ) from exc


# ============================================================
# 9. Excel TRUE/FALSE
# ============================================================

def _excel_to_bool(value):

    if isinstance(
        value,
        bool
    ):

        return value


    if isinstance(
        value,
        (int, float)
    ):

        return bool(value)


    text = str(
        value
    ).strip().upper()


    if text in (
        "TRUE",
        "T",
        "YES",
        "Y",
        "1"
    ):

        return True


    if text in (
        "FALSE",
        "F",
        "NO",
        "N",
        "0"
    ):

        return False


    raise ValueError(

        "TRUE/FALSE 값을 인식할 수 없습니다: "
        f"{value}"

    )


# ============================================================
# 10. QuantLib Date -> YYYY-MM-DD
# ============================================================

def _ql_date_to_text(date):

    return (

        f"{date.year():04d}-"
        f"{int(date.month()):02d}-"
        f"{date.dayOfMonth():02d}"

    )


# ============================================================
# 11. QuantLib Date -> YYYYMMDD
# ============================================================

def _ql_date_to_yyyymmdd(date):

    return (

        f"{date.year():04d}"
        f"{int(date.month()):02d}"
        f"{date.dayOfMonth():02d}"

    )


# ============================================================
# 12. YYYYMMDD -> QuantLib Date
# ============================================================

def _yyyymmdd_to_ql_date(value):

    text = str(value).strip()

    if len(text) != 8:

        raise ValueError(
            f"YYYYMMDD 형식이 아닙니다: {value}"
        )

    year = int(text[0:4])
    month = int(text[4:6])
    day = int(text[6:8])

    return ql.Date(
        day,
        month,
        year
    )


# ============================================================
# 13. Normalize Market Data Date
#
# Excel에서
#
# 20250101
# 20250101.0
# 2025-01-01
# datetime
#
# 등을 YYYYMMDD로 통일
# ============================================================

def _normalize_market_date(value):

    if pd.isna(value):

        return None


    # --------------------------------------------------------
    # Timestamp / datetime
    # --------------------------------------------------------

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


    text = str(
        value
    ).strip()


    if text.endswith(
        ".0"
    ):

        text = text[:-2]


    # --------------------------------------------------------
    # Already YYYYMMDD
    # --------------------------------------------------------

    if (
        len(text) == 8
        and text.isdigit()
    ):

        return text


    # --------------------------------------------------------
    # Parse date
    # --------------------------------------------------------

    try:

        timestamp = pd.to_datetime(
            text
        )

        return (

            f"{timestamp.year:04d}"
            f"{timestamp.month:02d}"
            f"{timestamp.day:02d}"

        )

    except Exception:

        raise ValueError(

            "Market Data Date를 "
            f"해석할 수 없습니다: {value}"

        )


# ============================================================
# 14. Read Excel Inputs
# ============================================================

def _read_excel_inputs(sheet):

    valuation_date = (
        _excel_date_to_ql_date(

            sheet.Range(
                CELL_VALUATION_DATE
            ).Value

        )
    )


    trade_date = (
        _excel_date_to_ql_date(

            sheet.Range(
                CELL_TRADE_DATE
            ).Value

        )
    )


    maturity = (
        _excel_date_to_ql_date(

            sheet.Range(
                CELL_MATURITY
            ).Value

        )
    )


    call_frequency = str(

        sheet.Range(
            CELL_CALL_FREQUENCY
        ).Value

    ).strip().upper()


    call_exists = (
        _excel_to_bool(

            sheet.Range(
                CELL_CALL_EXISTS
            ).Value

        )
    )


    fixed_rate = float(

        sheet.Range(
            CELL_FIXED_RATE
        ).Value

    )


    floating_spread = float(

        sheet.Range(
            CELL_FLOATING_SPREAD
        ).Value

    )


    notional = float(

        sheet.Range(
            CELL_NOTIONAL
        ).Value

    )


    floating_tenor = str(

        sheet.Range(
            CELL_FLOATING_TENOR
        ).Value

    ).strip().upper()


    receiver_payer = str(

        sheet.Range(
            CELL_RECEIVER_PAYER
        ).Value

    ).strip().upper()


    call_right = str(

        sheet.Range(
            CELL_CALL_RIGHT
        ).Value

    ).strip().upper()


    # ========================================================
    # Validation
    # ========================================================

    if trade_date > valuation_date:

        raise ValueError(

            "Trade Date가 Valuation Date보다 "
            "미래입니다."

        )


    if maturity <= valuation_date:

        raise ValueError(

            "Maturity는 Valuation Date보다 "
            "이후여야 합니다."

        )


    if notional <= 0.0:

        raise ValueError(
            "Notional은 0보다 커야 합니다."
        )


    if receiver_payer not in (
        "PAYER",
        "RECEIVER"
    ):

        raise ValueError(

            "Receiver / Payer는 "
            "PAYER 또는 RECEIVER여야 합니다."

        )


    if call_right not in (
        "PAYER",
        "RECEIVER"
    ):

        raise ValueError(

            "Call Right는 "
            "PAYER 또는 RECEIVER여야 합니다."

        )


    return {

        "valuation_date":
            valuation_date,

        "trade_date":
            trade_date,

        "maturity":
            maturity,

        "call_frequency":
            call_frequency,

        "call_exists":
            call_exists,

        "fixed_rate":
            fixed_rate,

        "floating_spread":
            floating_spread,

        "notional":
            notional,

        "floating_tenor":
            floating_tenor,

        "receiver_payer":
            receiver_payer,

        "call_right":
            call_right

    }


# ============================================================
# 15. Print Inputs
# ============================================================

def _print_inputs(inputs):

    print()

    print(
        "=" * 80
    )

    print(
        "Excel Input"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "Valuation Date   :",
        inputs["valuation_date"]
    )

    print(
        "Trade Date       :",
        inputs["trade_date"]
    )

    print(
        "Maturity         :",
        inputs["maturity"]
    )

    print(
        "Call Frequency   :",
        inputs["call_frequency"]
    )

    print(
        "Call Exists      :",
        inputs["call_exists"]
    )

    print(
        "Fixed Rate       :",
        inputs["fixed_rate"]
    )

    print(
        "Floating Spread  :",
        inputs["floating_spread"]
    )

    print(
        "Notional         :",
        inputs["notional"]
    )

    print(
        "CMS Tenor        :",
        inputs["floating_tenor"]
    )

    print(
        "Receiver / Payer :",
        inputs["receiver_payer"]
    )

    print(
        "Call Right       :",
        inputs["call_right"]
    )


# ============================================================
# 16. File Validation
# ============================================================

def _validate_input_files():

    if not os.path.isfile(
        YIELD_CURVE_FILE
    ):

        raise FileNotFoundError(

            "Yield Curve 파일을 찾을 수 없습니다:\n"
            f"{YIELD_CURVE_FILE}"

        )


    if not os.path.isfile(
        SWAPTION_VOL_FILE
    ):

        raise FileNotFoundError(

            "Swaption Vol 파일을 찾을 수 없습니다:\n"
            f"{SWAPTION_VOL_FILE}"

        )


# ============================================================
# 17. Read Available Market Dates
# ============================================================

def _read_market_dates(
    file_path,
    sheet_name=0
):

    data = pd.read_excel(
        file_path,
        sheet_name=sheet_name
    )


    if "Data" not in data.columns:

        raise ValueError(

            f"{os.path.basename(file_path)}에 "
            "'Data' 컬럼이 없습니다.\n\n"

            f"현재 컬럼:\n"
            f"{list(data.columns)}"

        )


    dates = []


    for value in data[
        "Data"
    ]:

        normalized = (
            _normalize_market_date(
                value
            )
        )

        if normalized is not None:

            dates.append(
                normalized
            )


    return sorted(
        set(dates)
    )


# ============================================================
# 18. Select Common Market Data Date
#
# 원칙:
#
# Yield Curve와 Swaption Vol에 모두 존재하면서
# Valuation Date 이하인 가장 최근 날짜
#
# 예:
#
# valuation = 20250103
#
# available:
#
# 20250101
# 20250201
#
# => 20250101
#
# Look-ahead data 사용 방지
# ============================================================

def _select_market_data_date(
    valuation_date
):

    valuation_key = (
        _ql_date_to_yyyymmdd(
            valuation_date
        )
    )


    yield_dates = (
        _read_market_dates(
            YIELD_CURVE_FILE
        )
    )


    vol_dates = (
        _read_market_dates(
            SWAPTION_VOL_FILE
        )
    )


    common_dates = sorted(

        set(yield_dates)

        .intersection(
            vol_dates
        )

    )


    eligible_dates = [

        date

        for date in common_dates

        if date <= valuation_key

    ]


    if len(
        eligible_dates
    ) == 0:

        raise ValueError(

            "Valuation Date 이하의 공통 Market Data Date가 "
            "없습니다.\n\n"

            f"Valuation Date : {valuation_key}\n"
            f"Yield Dates    : {yield_dates}\n"
            f"Swaption Dates : {vol_dates}"

        )


    market_data_date = (
        eligible_dates[-1]
    )


    return (
        market_data_date,
        yield_dates,
        vol_dates
    )


# ============================================================
# 19. Tenor -> Years
#
# 정렬용
# ============================================================

def _tenor_to_years(value):

    text = str(
        value
    ).strip().upper()


    if text.endswith(
        "Y"
    ):

        return float(
            text[:-1]
        )


    if text.endswith(
        "M"
    ):

        return (

            float(
                text[:-1]
            )

            / 12.0

        )


    if text.endswith(
        "W"
    ):

        return (

            float(
                text[:-1]
            )

            / 52.0

        )


    if text.endswith(
        "D"
    ):

        return (

            float(
                text[:-1]
            )

            / 365.0

        )


    raise ValueError(

        f"지원하지 않는 Tenor 형식입니다: {value}"

    )


# ============================================================
# 20. Load Swaption Vol Matrix
# ============================================================

def _load_swaption_vol_matrix(
    file_path,
    market_data_date
):

    data = pd.read_excel(
        file_path
    )


    # ========================================================
    # Data Date
    # ========================================================

    if "Data" not in data.columns:

        raise ValueError(

            "Swaption Vol 파일에 "
            "'Data' 컬럼이 없습니다."

        )


    data = data.copy()


    data[
        "_MarketDate"
    ] = data[
        "Data"
    ].map(
        _normalize_market_date
    )


    data = data[

        data[
            "_MarketDate"
        ]

        == market_data_date

    ].copy()


    if data.empty:

        raise ValueError(

            "선택된 Market Data Date의 "
            "Swaption Vol 데이터가 없습니다.\n\n"

            f"Market Data Date = "
            f"{market_data_date}"

        )


    # ========================================================
    # Column Recognition
    # ========================================================

    normalized_columns = {

        str(column)
        .strip()
        .lower():

            column

        for column in data.columns

    }


    expiry_column = None
    tenor_column = None
    vol_column = None


    # --------------------------------------------------------
    # Expiry
    # --------------------------------------------------------

    for candidate in (

        "옵션만기",

        "expiry",

        "option expiry",

        "option tenor",

        "optiontenor"

    ):

        key = candidate.lower()

        if key in normalized_columns:

            expiry_column = (
                normalized_columns[
                    key
                ]
            )

            break


    # --------------------------------------------------------
    # Underlying Swap Tenor
    # --------------------------------------------------------

    for candidate in (

        "기초자산만기",

        "swap tenor",

        "swaptenor",

        "underlying tenor",

        "tenor"

    ):

        key = candidate.lower()

        if key in normalized_columns:

            tenor_column = (
                normalized_columns[
                    key
                ]
            )

            break


    # --------------------------------------------------------
    # Volatility
    # --------------------------------------------------------

    for candidate in (

        "변동성",

        "vol",

        "volatility",

        "swaption vol"

    ):

        key = candidate.lower()

        if key in normalized_columns:

            vol_column = (
                normalized_columns[
                    key
                ]
            )

            break


    if expiry_column is None:

        raise ValueError(

            "Swaption Vol 파일에서 "
            "옵션만기 컬럼을 찾지 못했습니다."

        )


    if tenor_column is None:

        raise ValueError(

            "Swaption Vol 파일에서 "
            "기초자산만기 컬럼을 찾지 못했습니다."

        )


    if vol_column is None:

        raise ValueError(

            "Swaption Vol 파일에서 "
            "변동성 컬럼을 찾지 못했습니다."

        )


    # ========================================================
    # Normalize Tenors
    # ========================================================

    data[
        expiry_column
    ] = (

        data[
            expiry_column
        ]

        .astype(str)

        .str.strip()

        .str.upper()

    )


    data[
        tenor_column
    ] = (

        data[
            tenor_column
        ]

        .astype(str)

        .str.strip()

        .str.upper()

    )


    data[
        vol_column
    ] = pd.to_numeric(

        data[
            vol_column
        ],

        errors="coerce"

    )


    if data[
        vol_column
    ].isna().any():

        raise ValueError(

            "Swaption Vol 데이터에 "
            "숫자로 변환할 수 없는 변동성이 있습니다."

        )


    # ========================================================
    # Duplicate Check
    # ========================================================

    duplicate_mask = data.duplicated(

        subset=[
            expiry_column,
            tenor_column
        ],

        keep=False

    )


    if duplicate_mask.any():

        duplicate_rows = data.loc[

            duplicate_mask,

            [
                expiry_column,
                tenor_column,
                vol_column
            ]

        ]

        raise ValueError(

            "동일한 Option Expiry / Swap Tenor 조합이 "
            "중복되어 있습니다.\n\n"

            f"{duplicate_rows}"

        )


    # ========================================================
    # Long -> Matrix
    # ========================================================

    swaption_vols = data.pivot(

        index=
            expiry_column,

        columns=
            tenor_column,

        values=
            vol_column

    )


    # ========================================================
    # Correct Tenor Ordering
    #
    # 10Y,1Y,2Y...
    #
    # 같은 문자열 정렬을 방지
    # ========================================================

    row_order = sorted(

        swaption_vols.index,

        key=_tenor_to_years

    )


    column_order = sorted(

        swaption_vols.columns,

        key=_tenor_to_years

    )


    swaption_vols = swaption_vols.loc[

        row_order,

        column_order

    ]


    # ========================================================
    # NaN Validation
    # ========================================================

    if swaption_vols.isna().any().any():

        missing = (

            swaption_vols
            .isna()
        )

        raise ValueError(

            "Swaption Vol Matrix가 완전하지 않습니다.\n"
            "일부 Expiry / Tenor 조합의 Vol이 없습니다.\n\n"

            f"{missing}"

        )


    return swaption_vols



# ============================================================
# 21. Excel Output Helpers
# ============================================================

G2_CALIBRATION_SHEET_NAME = "G2Calibration"
SIMULATION_SHEET_NAME = "Simulation"
CALL_SCHEDULE_SHEET_NAME = "CallSchedule"
EXERCISE_SHEET_NAME = "Exercise"


def _get_or_create_sheet(workbook, sheet_name):
    """Return an existing worksheet or create it at the end."""
    try:
        return workbook.Worksheets(sheet_name)
    except Exception:
        sheet = workbook.Worksheets.Add(
            After=workbook.Worksheets(workbook.Worksheets.Count)
        )
        sheet.Name = sheet_name
        return sheet


def _clear_used_range(sheet):
    """Clear values only; keep workbook/template formatting."""
    try:
        sheet.UsedRange.ClearContents()
    except Exception:
        pass


def _clear_output(sheet):
    """Valuation input area is preserved; only old result area is cleared."""
    sheet.Range("A18:J200").ClearContents()


def _write_matrix(sheet, start_row, start_col, rows):
    """Write a rectangular Python list/tuple to Excel in one COM call."""
    if not rows:
        return

    normalized = []
    width = max(len(row) for row in rows)

    for row in rows:
        row = list(row) + [""] * (width - len(row))
        converted = []

        for value in row:
            if isinstance(value, ql.Date):
                value = _ql_date_to_text(value)
            elif isinstance(value, np.generic):
                value = value.item()
            elif pd.isna(value):
                value = ""

            converted.append(value)

        normalized.append(tuple(converted))

    end_row = start_row + len(normalized) - 1
    end_col = start_col + width - 1

    sheet.Range(
        sheet.Cells(start_row, start_col),
        sheet.Cells(end_row, end_col)
    ).Value = tuple(normalized)


def _autofit_sheet(sheet, max_width=28):
    """AutoFit and cap excessively wide columns."""
    try:
        sheet.UsedRange.Columns.AutoFit()
        count = int(sheet.UsedRange.Columns.Count)

        for j in range(1, count + 1):
            col = sheet.Columns(j)
            if col.ColumnWidth > max_width:
                col.ColumnWidth = max_width
    except Exception:
        pass


def _style_header(sheet, cell_range):
    """Small amount of formatting without depending on a template style."""
    try:
        rng = sheet.Range(cell_range)
        rng.Font.Bold = True
    except Exception:
        pass


# ============================================================
# 22. Write Error
# ============================================================

def _write_error(sheet, error):
    _clear_output(sheet)

    sheet.Range("A18").Value = "ERROR"
    sheet.Range("B18").Value = str(error)

    try:
        sheet.Range("B18").WrapText = True
        sheet.Columns("A:B").AutoFit()
    except Exception:
        pass


# ============================================================
# 23. Valuation Sheet
# ============================================================

def _write_valuation_sheet(
    sheet,
    result,
    simulation,
    market_data_date
):
    _clear_output(sheet)

    output = [
        ("VALUATION RESULT", ""),
        ("Valuation Date", _ql_date_to_text(result["Valuation Date"])),
        ("Market Data Date", str(market_data_date)),
        ("Trade Date", _ql_date_to_text(result["Trade Date"])),
        ("Maturity", _ql_date_to_text(result["Maturity"])),
        ("Receiver / Payer", result["Receiver / Payer"]),
        ("Call Exists", result["Call Exists"]),
        (
            "Call Right",
            result["Call Right"] if result["Call Right"] is not None else ""
        ),
        ("Zero Curve PV", result["Zero Curve PV"]),
    ]

    if result["Call Exists"]:
        output.extend([
            ("G2++ Straight Swap PV", result["G2++ Straight Swap PV"]),
            ("G2++ Straight Swap SE", result["G2++ Straight Swap SE"]),
            (
                "G2++ Straight 95% CI Lower",
                result["G2++ Straight 95% CI Lower"]
            ),
            (
                "G2++ Straight 95% CI Upper",
                result["G2++ Straight 95% CI Upper"]
            ),
            ("Callable CMS Swap PV", result["Callable CMS Swap PV"]),
            ("Callable CMS Swap SE", result["Callable CMS Swap SE"]),
            ("Callable 95% CI Lower", result["Callable 95% CI Lower"]),
            ("Callable 95% CI Upper", result["Callable 95% CI Upper"]),
            ("Callable Option Value", result["Callable Option Value"]),
            ("G2++ - Zero Curve", result["G2++ - Zero Curve"]),
            ("Never Exercised Paths", result["Never Exercised Paths"]),
            ("Never Exercised %", result["Never Exercised %"]),
        ])
    else:
        output.extend([
            ("G2++ PV", result["G2++ PV"]),
            ("G2++ PV SE", result["G2++ PV SE"]),
            ("G2++ 95% CI Lower", result["G2++ 95% CI Lower"]),
            ("G2++ 95% CI Upper", result["G2++ 95% CI Upper"]),
            ("G2++ - Zero Curve", result["G2++ - Zero Curve"]),
        ])

    output.extend([
        ("", ""),
        ("MONTE CARLO", ""),
        ("Number of Paths", simulation["n_paths"]),
        ("Time Step", simulation["dt"]),
        ("Number of Time Points", len(simulation["times"])),
    ])

    _write_matrix(sheet, OUTPUT_START_ROW, 1, output)
    _style_header(sheet, f"A{OUTPUT_START_ROW}:B{OUTPUT_START_ROW}")

    # Market Data Date is deliberately text, not an Excel date serial.
    try:
        sheet.Range(f"B{OUTPUT_START_ROW + 2}").NumberFormat = "@"
    except Exception:
        pass

    _autofit_sheet(sheet)


# ============================================================
# 24. G2Calibration Sheet
# ============================================================

def _write_g2_calibration_sheet(
    sheet,
    calibration_result,
    calibration_info,
    market_data_date
):
    _clear_used_range(sheet)

    parameters = calibration_info.get("parameters", {})

    summary = [
        ("G2++ CALIBRATION", ""),
        ("Market Data Date", str(market_data_date)),
        ("a", parameters.get("a", "")),
        ("sigma", parameters.get("sigma", "")),
        ("b", parameters.get("b", "")),
        ("eta", parameters.get("eta", "")),
        ("rho", parameters.get("rho", "")),
        ("Calibration Price MAE", calibration_info.get("price_mae", "")),
        ("Calibration Price RMSE", calibration_info.get("price_rmse", "")),
    ]

    _write_matrix(sheet, 1, 1, summary)

    if isinstance(calibration_result, pd.DataFrame):
        start_row = 12
        headers = [str(c) for c in calibration_result.columns]
        rows = [tuple(headers)]

        for row in calibration_result.itertuples(index=False, name=None):
            rows.append(tuple(row))

        _write_matrix(sheet, start_row, 1, rows)
        _style_header(
            sheet,
            f"A{start_row}:{chr(64 + min(len(headers), 26))}{start_row}"
        )

    _style_header(sheet, "A1:B1")
    _autofit_sheet(sheet)


# ============================================================
# 25. Simulation Sheet
# ============================================================

def _write_simulation_sheet(sheet, simulation):
    _clear_used_range(sheet)

    times = np.asarray(simulation["times"], dtype=float)
    discount_factors = np.asarray(
        simulation["discount_factors"],
        dtype=float
    )
    curve = simulation["curve"]

    n_paths = discount_factors.shape[0]

    mc_df = np.mean(discount_factors, axis=0)

    if n_paths > 1:
        mc_df_se = (
            np.std(discount_factors, axis=0, ddof=1)
            / np.sqrt(n_paths)
        )
    else:
        mc_df_se = np.zeros_like(mc_df)

    market_df = np.array(
        [float(curve.discount(float(t))) for t in times],
        dtype=float
    )

    market_zero = np.full_like(times, np.nan, dtype=float)
    mc_zero = np.full_like(times, np.nan, dtype=float)

    positive = times > 1.0e-12

    market_zero[positive] = (
        -np.log(np.maximum(market_df[positive], 1.0e-300))
        / times[positive]
    )

    mc_zero[positive] = (
        -np.log(np.maximum(mc_df[positive], 1.0e-300))
        / times[positive]
    )

    zero_error = mc_zero - market_zero
    df_error = mc_df - market_df

    rows = [[
        "Time (Years)",
        "Market DF",
        "MC DF",
        "MC DF SE",
        "DF Error",
        "Market Zero Rate",
        "MC Zero Rate",
        "Zero Rate Error"
    ]]

    for i in range(len(times)):
        rows.append([
            float(times[i]),
            float(market_df[i]),
            float(mc_df[i]),
            float(mc_df_se[i]),
            float(df_error[i]),
            "" if not positive[i] else float(market_zero[i]),
            "" if not positive[i] else float(mc_zero[i]),
            "" if not positive[i] else float(zero_error[i]),
        ])

    _write_matrix(sheet, 1, 1, rows)
    _style_header(sheet, "A1:H1")

    try:
        sheet.Range(
            f"F2:H{len(rows)}"
        ).NumberFormat = "0.000000%"
    except Exception:
        pass

    _autofit_sheet(sheet)


# ============================================================
# 26. CallSchedule Sheet
# ============================================================

def _write_call_schedule_sheet(
    sheet,
    call_dates,
    valuation_date,
    simulation
):
    _clear_used_range(sheet)

    dt = float(simulation["dt"])
    day_counter = ql.Actual365Fixed()

    rows = [[
        "No.",
        "Call Date",
        "Time (Years)",
        "Simulation Index"
    ]]

    for i, date in enumerate(call_dates, start=1):
        t = float(
            day_counter.yearFraction(
                valuation_date,
                date
            )
        )
        simulation_index = int(round(t / dt))

        rows.append([
            i,
            _ql_date_to_text(date),
            t,
            simulation_index
        ])

    _write_matrix(sheet, 1, 1, rows)
    _style_header(sheet, "A1:D1")

    # Write dates as text so Excel never shows ########.
    try:
        sheet.Columns("B").NumberFormat = "@"
    except Exception:
        pass

    _autofit_sheet(sheet)


# ============================================================
# 27. Exercise Sheet
# ============================================================

def _write_exercise_sheet(sheet, result):
    _clear_used_range(sheet)

    if not result["Call Exists"]:
        _write_matrix(
            sheet,
            1,
            1,
            [
                ("EXERCISE STATISTICS", ""),
                ("Call Exists", False),
            ]
        )
        _autofit_sheet(sheet)
        return

    exercise_df = result["Exercise Statistics"].copy()

    if "Call Date" in exercise_df.columns:
        exercise_df["Call Date"] = (
            exercise_df["Call Date"].map(_ql_date_to_text)
        )

    rows = [[str(c) for c in exercise_df.columns]]

    for row in exercise_df.itertuples(index=False, name=None):
        rows.append(list(row))

    rows.append([])
    rows.append([
        "Never Exercised Paths",
        result["Never Exercised Paths"]
    ])
    rows.append([
        "Never Exercised %",
        result["Never Exercised %"]
    ])

    _write_matrix(sheet, 1, 1, rows)
    _style_header(
        sheet,
        f"A1:{chr(64 + min(len(exercise_df.columns), 26))}1"
    )

    try:
        # Exercise % values in CallableCMS.py are already 0~100,
        # so use a numeric format rather than Excel percentage format.
        if "Exercise %" in exercise_df.columns:
            col_idx = list(exercise_df.columns).index("Exercise %") + 1
            sheet.Columns(col_idx).NumberFormat = "0.0000"
    except Exception:
        pass

    _autofit_sheet(sheet)


# ============================================================
# 28. Write Results to All Excel Sheets
# ============================================================

def _write_result_to_excel(
    workbook,
    result,
    calibration_result,
    calibration_info,
    simulation,
    market_data_date,
    call_dates
):
    valuation_sheet = _get_or_create_sheet(
        workbook,
        VALUATION_SHEET_NAME
    )
    g2_sheet = _get_or_create_sheet(
        workbook,
        G2_CALIBRATION_SHEET_NAME
    )
    simulation_sheet = _get_or_create_sheet(
        workbook,
        SIMULATION_SHEET_NAME
    )
    call_schedule_sheet = _get_or_create_sheet(
        workbook,
        CALL_SCHEDULE_SHEET_NAME
    )
    exercise_sheet = _get_or_create_sheet(
        workbook,
        EXERCISE_SHEET_NAME
    )

    _write_valuation_sheet(
        valuation_sheet,
        result,
        simulation,
        market_data_date
    )

    _write_g2_calibration_sheet(
        g2_sheet,
        calibration_result,
        calibration_info,
        market_data_date
    )

    _write_simulation_sheet(
        simulation_sheet,
        simulation
    )

    _write_call_schedule_sheet(
        call_schedule_sheet,
        call_dates,
        result["Valuation Date"],
        simulation
    )

    _write_exercise_sheet(
        exercise_sheet,
        result
    )


# ============================================================
# 25. Find Workbook
#
# ActiveWorkbook만 의존하지 않도록 보강
# ============================================================

def _get_workbook(excel):

    # ========================================================
    # 1. Active Workbook
    # ========================================================

    workbook = (
        excel.ActiveWorkbook
    )


    if workbook is not None:

        return workbook


    # ========================================================
    # 2. Search Open Workbooks
    # ========================================================

    for i in range(
        1,
        excel.Workbooks.Count + 1
    ):

        candidate = (
            excel.Workbooks.Item(
                i
            )
        )


        if (
            str(candidate.Name).lower()
            ==
            EXPECTED_WORKBOOK_NAME.lower()
        ):

            return candidate


    raise RuntimeError(

        "평가용 Excel Workbook을 찾을 수 없습니다.\n\n"

        f"먼저 '{EXPECTED_WORKBOOK_NAME}'을 "
        "Excel에서 열어주세요."

    )


# ============================================================
# 26. Main
# ============================================================

def main():

    print()

    print(
        "=" * 100
    )

    print(
        "Callable CMS Swap Valuation"
    )

    print(
        "=" * 100
    )


    # ========================================================
    # A. Excel Connection
    # ========================================================

    excel = (
        win32com.client
        .GetActiveObject(
            "Excel.Application"
        )
    )


    workbook = (
        _get_workbook(
            excel
        )
    )


    sheet = workbook.Worksheets(
        VALUATION_SHEET_NAME
    )


    print()

    print(
        "Excel Workbook :",
        workbook.Name
    )


    # ========================================================
    # B. Excel Input
    # ========================================================

    inputs = (
        _read_excel_inputs(
            sheet
        )
    )


    _print_inputs(
        inputs
    )


    valuation_date = (
        inputs[
            "valuation_date"
        ]
    )

    trade_date = (
        inputs[
            "trade_date"
        ]
    )

    maturity = (
        inputs[
            "maturity"
        ]
    )


    # ========================================================
    # C. File Validation
    # ========================================================

    _validate_input_files()


    # ========================================================
    # D. Market Data Date Selection
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "0. Market Data Date Selection"
    )

    print(
        "=" * 100
    )


    (

        market_data_date,

        yield_dates,

        vol_dates

    ) = _select_market_data_date(
        valuation_date
    )


    print()

    print(
        "Valuation Date  :",
        _ql_date_to_yyyymmdd(
            valuation_date
        )
    )

    print(
        "Market Data Date:",
        market_data_date
    )


    if (
        market_data_date
        !=
        _ql_date_to_yyyymmdd(
            valuation_date
        )
    ):

        print()

        print(
            "[INFO]"
        )

        print(

            "Valuation Date와 동일한 Market Data가 "
            "없으므로 Valuation Date 이하의 가장 최근 "
            "공통 Market Data Date를 사용합니다."

        )


    # ========================================================
    # E. Yield Curve Bootstrap
    #
    # IMPORTANT:
    #
    # 현재 Bootstrapping.py는 calibration_date를
    # curve reference/evaluation date로 사용함.
    #
    # 따라서 우선 Market Data Date로 curve를 생성.
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "1. Yield Curve Bootstrap"
    )

    print(
        "=" * 100
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


    print()

    print(
        "Yield Curve Bootstrap 완료"
    )


    # ========================================================
    # F. Swaption Vol Matrix
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "2. Swaption Volatility"
    )

    print(
        "=" * 100
    )


    swaption_vols = (
        _load_swaption_vol_matrix(

            SWAPTION_VOL_FILE,

            market_data_date

        )
    )


    print()

    print(
        "Market Data Date:",
        market_data_date
    )

    print()

    print(
        swaption_vols
    )


    # ========================================================
    # G. IMPORTANT:
    #
    # G2 calibration은 curve reference date와 동일한
    # Market Data Date 기준으로 수행.
    # ========================================================

    calibration_ql_date = (
        _yyyymmdd_to_ql_date(
            market_data_date
        )
    )


    ql.Settings.instance().evaluationDate = (
        calibration_ql_date
    )


    # ========================================================
    # H. G2++ Calibration
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "3. G2++ Calibration"
    )

    print(
        "=" * 100
    )


    try:

        (

            model,

            calibration_result,

            calibration_info

        ) = calibrate_g2(

            curve=
                curve,

            swaption_vols=
                swaption_vols,

            today=
                calibration_ql_date,

            fixed_leg_tenor=
                "1Y",

            initial_params=(

                0.10,
                0.01,
                0.30,
                0.015,
                -0.70

            ),

            engine_range=
                6.0,

            engine_intervals=
                16,

            calculate_implied_vol=
                False

        )

    except RuntimeError as exc:

        message = str(
            exc
        )


        if (
            "root not bracketed"
            in message.lower()
        ):

            raise RuntimeError(

                "\nG2++ Calibration에서 "
                "'root not bracketed' 오류가 발생했습니다.\n\n"

                "Market Data Date 및 Swaption Matrix는 "
                "Runner에서 정상 필터링/정렬되었습니다.\n\n"

                "따라서 다음 확인 대상은 "
                "SwaptionVolCalib.py의 G2 parameter "
                "constraint 및 calibration 설정입니다.\n\n"

                f"QuantLib original error:\n"
                f"{message}"

            ) from exc


        raise


    print()

    print(
        "G2++ Calibration 완료"
    )


    # ========================================================
    # I. Print Parameters
    # ========================================================

    parameters = calibration_info.get(
        "parameters",
        {}
    )


    print()

    print(
        "a     :",
        parameters.get(
            "a"
        )
    )

    print(
        "sigma :",
        parameters.get(
            "sigma"
        )
    )

    print(
        "b     :",
        parameters.get(
            "b"
        )
    )

    print(
        "eta   :",
        parameters.get(
            "eta"
        )
    )

    print(
        "rho   :",
        parameters.get(
            "rho"
        )
    )


    # ========================================================
    # J. Pricing Date
    #
    # IMPORTANT
    #
    # 현재 Curve/G2 model은 Market Data Date 기준.
    #
    # Exact simulation과 Callable valuation은
    # simulation["today"]와 valuation_date가 같아야 함.
    #
    # 현재 구조에서는 Market Data Date와 Valuation Date가
    # 다른 경우 model/curve를 그대로 Valuation Date로
    # 이동시키는 것은 엄밀하지 않음.
    #
    # 따라서 현재 단계에서는 날짜 불일치를 명시적으로
    # 차단한다.
    #
    # 추후 Excel에 Market Data Date를 별도로 두고
    # Curve Roll/Valuation Date 구조를 구현할 수 있음.
    # ========================================================

    valuation_key = (
        _ql_date_to_yyyymmdd(
            valuation_date
        )
    )


    if (
        valuation_key
        !=
        market_data_date
    ):

        raise ValueError(

            "\n현재 Market Data Date와 "
            "Valuation Date가 다릅니다.\n\n"

            f"Valuation Date   = {valuation_key}\n"
            f"Market Data Date = {market_data_date}\n\n"

            "G2 Calibration까지는 Market Data Date 기준으로 "
            "정상 수행할 수 있지만, 현재 Exact Simulation과 "
            "CallableCMS는 valuation_date와 curve/model 기준일이 "
            "동일한 구조입니다.\n\n"

            "테스트를 계속하려면 우선 Excel B5의 "
            "Valuation Date를 Market Data Date와 동일하게 "
            "설정하세요.\n\n"

            "예: B5 = 2025-01-01\n\n"

            "이후 전체 파이프라인이 정상 동작하는 것을 "
            "확인한 다음 Market Data Date와 Valuation Date를 "
            "분리하는 구조를 추가하는 것이 안전합니다."

        )


    # ========================================================
    # K. Evaluation Date
    # ========================================================

    ql.Settings.instance().evaluationDate = (
        valuation_date
    )


    # ========================================================
    # L. Simulation Horizon
    # ========================================================

    day_counter = ql.Actual365Fixed()


    maturity_years = (
        day_counter.yearFraction(

            valuation_date,

            maturity

        )
    )


    simulation_years = int(

        np.ceil(
            maturity_years
        )

        + 1

    )


    # ========================================================
    # M. Exact G2++ Monte Carlo
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "4. Exact G2++ Monte Carlo Simulation"
    )

    print(
        "=" * 100
    )


    simulation = (
        simulate_g2_short_rate(

            curve=
                curve,

            model=
                model,

            simulation_years=
                simulation_years,

            n_paths=
                N_PATHS,

            time_step_months=
                TIME_STEP_MONTHS,

            seed=
                RANDOM_SEED,

            store_factors=
                True,

            store_integrated_rates=
                False

        )
    )


    print()

    print(
        "Paths      :",
        f"{simulation['n_paths']:,}"
    )

    print(
        "Time Points:",
        f"{len(simulation['times']):,}"
    )

    print(
        "dt         :",
        simulation[
            "dt"
        ]
    )


    # ========================================================
    # N. Call Dates
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "5. Call Date Generation"
    )

    print(
        "=" * 100
    )


    if inputs[
        "call_exists"
    ]:

        call_dates = (
            generate_call_dates(

                trade_date,

                maturity,

                valuation_date,

                inputs[
                    "call_frequency"
                ]

            )
        )

    else:

        call_dates = []


    print()

    print(
        "Number of Call Dates:",
        len(
            call_dates
        )
    )


    for date in call_dates:

        print(
            " ",
            _ql_date_to_text(
                date
            )
        )


    # ========================================================
    # O. Callable CMS Valuation
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "6. Callable CMS Swap Valuation"
    )

    print(
        "=" * 100
    )


    result = (
        price_callable_cms_swap(

            simulation=
                simulation,

            trade_date=
                trade_date,

            valuation_date=
                valuation_date,

            maturity=
                maturity,

            fixed_rate=
                inputs[
                    "fixed_rate"
                ],

            floating_spread=
                inputs[
                    "floating_spread"
                ],

            notional=
                inputs[
                    "notional"
                ],

            floating_tenor=
                inputs[
                    "floating_tenor"
                ],

            call_exists=
                inputs[
                    "call_exists"
                ],

            call_dates=
                call_dates,

            receiver_payer=
                inputs[
                    "receiver_payer"
                ],

            call_right=
                inputs[
                    "call_right"
                ],

            payment_tenor=
                "3M"

        )
    )


    # ========================================================
    # P. Console Result
    # ========================================================

    print_callable_cms_swap_result(
        result
    )


    # ========================================================
    # Q. Excel Result
    # ========================================================

    print()

    print(
        "=" * 100
    )

    print(
        "7. Write Results to Excel"
    )

    print(
        "=" * 100
    )


    _write_result_to_excel(
        workbook=workbook,
        result=result,
        calibration_result=calibration_result,
        calibration_info=calibration_info,
        simulation=simulation,
        market_data_date=market_data_date,
        call_dates=call_dates
    )


    # ========================================================
    # R. Save Workbook
    # ========================================================

    workbook.Save()


    print()

    print(
        "=" * 100
    )

    print(
        "Callable CMS Swap Valuation Completed"
    )

    print(
        "=" * 100
    )

    print()

    print(
        "Excel 각 결과 시트에 "
        "평가 결과가 기록되었습니다."
    )


# ============================================================
# 27. Execute
# ============================================================

if __name__ == "__main__":

    try:

        main()


    except Exception as error:

        print()

        print(
            "=" * 100
        )

        print(
            "ERROR"
        )

        print(
            "=" * 100
        )

        print()

        print(
            str(
                error
            )
        )

        print()

        traceback.print_exc()


        # ====================================================
        # Write Error to Excel if possible
        # ====================================================

        try:

            excel = (
                win32com.client
                .GetActiveObject(
                    "Excel.Application"
                )
            )


            workbook = (
                _get_workbook(
                    excel
                )
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