from __future__ import annotations

import sys
import threading
import zipfile
from io import BytesIO
from datetime import date
from pathlib import Path
import re
from xml.etree import ElementTree

import numpy as np
import pandas as pd
import QuantLib as ql
from flask import Flask, jsonify, render_template, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_TOOL_PATH = PROJECT_ROOT / "RunTool"
FUNCTION_PATH = PROJECT_ROOT / "Function"

for path in (RUN_TOOL_PATH, FUNCTION_PATH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import RunCallableHybridBond as runner  # noqa: E402
from Callablehybridbond import price_callable_hybrid_bond  # noqa: E402


app = Flask(__name__)
simulation_lock = threading.Lock()


def _pptx_text(slide_xml: bytes) -> list[str]:
    root = ElementTree.fromstring(slide_xml)
    namespace = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    return [text.text for text in root.iter(f"{namespace}t") if text.text]


def inspect_pptx(file_storage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValueError("PPTX 파일을 선택하세요.")
    if not file_storage.filename.lower().endswith(".pptx"):
        raise ValueError(".pptx 파일만 업로드할 수 있습니다.")

    payload = file_storage.read()
    if len(payload) > 25 * 1024 * 1024:
        raise ValueError("파일 크기는 25MB 이하만 업로드할 수 있습니다.")
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        slide_names = sorted(
            [
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ],
            key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)),
        )
        if not slide_names:
            raise ValueError("슬라이드가 없는 PPTX 파일입니다.")
        slides = []
        for index, name in enumerate(slide_names, start=1):
            texts = _pptx_text(archive.read(name))
            slides.append({
                "number": index,
                "title": texts[0] if texts else f"Slide {index}",
                "texts": texts,
                "text_count": len(texts),
            })
    return {"filename": file_storage.filename, "slide_count": len(slides), "slides": slides}


def _parse_date(value: str, field_name: str) -> ql.Date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}는 YYYY-MM-DD 형식이어야 합니다.") from exc
    return ql.Date(parsed.day, parsed.month, parsed.year)


def _number(payload: dict, name: str, *, default=None, minimum=None) -> float:
    raw = payload.get(name, default)
    if raw is None or raw == "":
        if default is not None:
            return float(default)
        raise ValueError(f"{name} 값을 입력하세요.")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}는 숫자여야 합니다.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}는 {minimum} 이상이어야 합니다.")
    return value


def _json_safe(value):
    if isinstance(value, ql.Date):
        return f"{value.year():04d}-{value.month():02d}-{value.dayOfMonth():02d}"
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return None
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _web_result(result: dict) -> dict:
    hidden_path_fields = {
        "Path Values",
        "Reset Benchmark YTM Paths",
        "Post Reset Coupon Rate Paths",
    }
    compact = {}
    for key, value in result.items():
        if key in hidden_path_fields:
            continue
        if isinstance(value, pd.DataFrame):
            compact[key] = value.to_dict(orient="records")
        else:
            compact[key] = value
    return _json_safe(compact)


def evaluate(payload: dict) -> dict:
    valuation_date = _parse_date(payload.get("valuation_date"), "평가일")
    issue_date = _parse_date(payload.get("issue_date"), "발행일")
    maturity = _parse_date(payload.get("maturity"), "만기일")
    first_call_date = _parse_date(payload.get("first_call_date"), "최초 콜/리셋일")

    reset_mode = str(payload.get("reset_rate_mode", "FIXED")).upper()
    if reset_mode not in {"FIXED", "YTM_PLUS_SPREAD"}:
        raise ValueError("리셋 방식은 FIXED 또는 YTM_PLUS_SPREAD여야 합니다.")

    reset_fixed_rate = None
    if reset_mode == "FIXED":
        reset_fixed_rate = _number(payload, "reset_fixed_rate", minimum=0)

    with simulation_lock:
        ql.Settings.instance().evaluationDate = valuation_date
        simulation = runner.get_or_build_g2_simulation(valuation_date)
        result = price_callable_hybrid_bond(
            simulation=simulation,
            valuation_date=valuation_date,
            issue_date=issue_date,
            maturity=maturity,
            first_call_date=first_call_date,
            initial_coupon_rate=_number(payload, "initial_coupon_rate", minimum=0),
            coupon_frequency=payload.get("coupon_frequency", "3M"),
            call_frequency=payload.get("call_frequency", "3M"),
            reset_rate_mode=reset_mode,
            reset_fixed_rate=reset_fixed_rate,
            benchmark_tenor=payload.get("benchmark_tenor", "5Y"),
            benchmark_frequency=payload.get("benchmark_frequency", "6M"),
            reset_spread=_number(payload, "reset_spread", default=0, minimum=0),
            reset_coupon_rate_override=None,
            notional=_number(payload, "notional", minimum=0),
            call_price_pct=_number(payload, "call_price_pct", minimum=0),
        )

    return _web_result(result)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/studio")
def studio():
    return render_template("studio.html")


@app.post("/api/pptx/inspect")
def inspect_uploaded_pptx():
    try:
        return jsonify({"ok": True, "deck": inspect_pptx(request.files.get("file"))})
    except (ValueError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/g2simulation")
def g2simulation_slides():
    return render_template("g2simulation.html")


@app.get("/callableswap")
def callableswap_slides():
    return render_template("callableswap.html")


@app.get("/valuation")
def valuation_slides():
    return render_template("valuation.html")


@app.post("/api/price")
def price():
    try:
        payload = request.get_json(silent=True) or request.form.to_dict()
        return jsonify({"ok": True, "result": evaluate(payload)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)