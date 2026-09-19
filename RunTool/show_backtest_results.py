"""Re-render completed backtest results: %run -i <this file> in its notebook kernel."""

import json as _result_json
from pathlib import Path as _ResultPath

_required_results = {
    "backtest_summary", "daily_portfolio", "trade_log", "final_holdings",
    "initial_capital", "BACKTEST_START", "BACKTEST_END",
}
_missing_results = sorted(_required_results.difference(globals()))
if _missing_results:
    raise RuntimeError(
        "계산을 완료한 노트북 커널에서 %run -i로 실행하세요. 없는 변수: "
        + ", ".join(_missing_results)
    )

_result_notebook = _ResultPath(__file__).resolve().parents[1] / "NoteBook" / "NoteBook.ipynb"
_saved_result_notebook = _result_json.loads(_result_notebook.read_text(encoding="utf-8"))
_result_source = next(
    "".join(cell["source"])
    for cell in _saved_result_notebook["cells"]
    if cell["cell_type"] == "code" and "BACKTEST_START =" in "".join(cell.get("source", []))
)
_result_section = _result_source[_result_source.index("# 14. Result"):]
print(f"결과만 다시 출력합니다. 원본: {_result_notebook}")
exec(compile(_result_section, str(_result_notebook) + ":results", "exec"), globals())
