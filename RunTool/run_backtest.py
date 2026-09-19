"""Run only the notebook's backtest cell and save its actual outputs (no orders)."""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import FinanceDataReader as fdr
import pandas as pd
from IPython.core.formatters import DisplayFormatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Function.KoreanStockQuotes import analyze_technical_indicators


def main():
    path = ROOT / "NoteBook" / "NoteBook.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    cell = next(c for c in notebook["cells"] if "BACKTEST_START =" in "".join(c.get("source", [])))
    source = "".join(cell["source"])
    signature = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    started = datetime.now().isoformat(timespec="seconds")
    outputs = []
    formatter = DisplayFormatter()

    class Tee(io.TextIOBase):
        def write(self, value):
            sys.__stdout__.write(value)
            sys.__stdout__.flush()
            if outputs and outputs[-1]["output_type"] == "stream":
                outputs[-1]["text"] += value
            else:
                outputs.append({"output_type": "stream", "name": "stdout", "text": value})
            return len(value)

        def flush(self):
            sys.__stdout__.flush()

    def display_result(value):
        data, metadata = formatter.format(value)
        outputs.append({"output_type": "display_data", "data": data, "metadata": metadata})

    def show_plot(*args, **kwargs):
        for number in plt.get_fignums():
            figure = plt.figure(number)
            buffer = io.BytesIO()
            figure.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
            outputs.append({"output_type": "display_data", "metadata": {}, "data": {
                "image/png": base64.b64encode(buffer.getvalue()).decode("ascii"),
                "text/plain": figure.axes[0].get_title() if figure.axes else "Figure",
            }})
            plt.close(figure)

    scope = {"fdr": fdr, "analyze_technical_indicators": analyze_technical_indicators,
             "display": display_result, "__name__": "__main__"}
    with redirect_stdout(Tee()), patch.object(plt, "show", show_plot):
        print(f"BACKTEST RUN {started} | source {signature}")
        print(f"LOCAL NOTEBOOK: {path.resolve()}")
        print(f"PYTHON: {sys.executable}")
        for line in source.splitlines():
            if line.startswith(("BACKTEST_START =", "BACKTEST_END =", "UNIT_INVESTMENT =", "MAX_INVESTMENT =")):
                print(line)
        exec(compile(source, str(path) + ":backtest", "exec"), scope)
        trades = scope["trade_log"]
        buys = trades.loc[trades["구분"].eq("매수")] if not trades.empty else trades
        print("\nACTUAL RUN DIAGNOSTICS")
        print(f"Loaded stocks: {len(scope['indicator_histories'])}")
        print(f"Initial capital: {scope['initial_capital']:,.0f}")
        print(f"Buy count: {len(buys)}")
        if not buys.empty:
            print(f"Last buy: {buys['일자'].max():%Y-%m-%d}")
            print(buys.groupby(buys["일자"].dt.to_period("M")).size().to_string())
        comparison = scope.get("kospi_comparison_summary")
        if comparison is None:
            raise RuntimeError("KOSPI comparison did not complete; results will not be marked successful.")
        print(comparison.to_string())
        daily = scope["daily_portfolio"]
        assert (daily["현금잔액"] >= -1e-6).all()
        assert ((daily["총자산"] - daily["총손익"] - scope["initial_capital"]).abs() < 0.1).all()

    # Re-read to preserve other edits and refuse to attach results to changed code.
    current = json.loads(path.read_text(encoding="utf-8"))
    target = next(c for c in current["cells"] if c.get("id") == cell["id"])
    if "".join(target["source"]) != source:
        raise RuntimeError("Backtest source changed during execution; refusing to attach stale outputs.")
    target["outputs"] = outputs
    target["execution_count"] = 1
    path.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # A standalone report also avoids confusion with an already-open notebook buffer.
    pieces = ["<!doctype html><html lang='ko'><meta charset='utf-8'>",
              "<title>Backtest Result</title><style>body{font:15px sans-serif;margin:32px}img{max-width:100%}table{border-collapse:collapse}th,td{padding:6px;border:1px solid #ddd}pre{white-space:pre-wrap}</style>",
              f"<h1>Backtest Result</h1><p>{html.escape(started)} | source {signature}</p>"]
    for output in outputs:
        if output["output_type"] == "stream":
            pieces.append("<pre>" + html.escape(output["text"]) + "</pre>")
        else:
            data = output["data"]
            if "image/png" in data:
                pieces.append('<img src="data:image/png;base64,' + data["image/png"] + '">')
            elif "text/html" in data:
                pieces.append(data["text/html"])
            else:
                pieces.append("<pre>" + html.escape(data.get("text/plain", "")) + "</pre>")
    pieces.append("</html>")
    report = ROOT / "NoteBook" / "Backtest_Result.html"
    report.write_text("\n".join(pieces), encoding="utf-8")
    print(f"Saved executed notebook and {report}")


if __name__ == "__main__":
    main()
