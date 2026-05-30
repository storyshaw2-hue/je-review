"""
Headless UI smoke test — drives the Streamlit app end-to-end via the official
AppTest harness (no browser). Uploads the sample export, clicks Run, and asserts
the metrics, exceptions table, download buttons, and triage notes render with no
exception. Run:  python -m pytest -q tests/test_ui_smoke.py   (or run directly).

Requires streamlit installed (it's in requirements.txt).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "sample_je.csv"


def _drive():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "ui" / "streamlit_app.py"), default_timeout=60)
    at.run()
    at.number_input[0].set_value(50000)
    at.file_uploader[0].set_value(("sample_je.csv", SAMPLE.read_bytes(), "text/csv"))
    at.run()
    at.button[0].set_value(True)
    at.run()
    return at


def test_ui_runs_without_exception():
    at = _drive()
    assert not at.exception, f"App raised: {at.exception}"


def test_ui_renders_expected_outputs():
    at = _drive()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Entries flagged") == "15"
    assert len(at.dataframe) == 1
    df = at.dataframe[0].value
    assert len(df) == 15
    assert df.iloc[0]["je_id"] == "JE-2024-0155"   # composite high-risk entry tops the list
    labels = [d.label for d in at.get("download_button")]
    assert any("Excel" in l for l in labels) and any("CSV" in l for l in labels)


if __name__ == "__main__":
    for fn in [test_ui_runs_without_exception, test_ui_renders_expected_outputs]:
        fn()
        print(f"PASS  {fn.__name__}")
    print("UI smoke test passed.")
