"""Headless UI smoke test via Streamlit's AppTest (no browser)."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "sample_je.csv"


def _drive():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "ui" / "streamlit_app.py"), default_timeout=90)
    at.run()
    at.number_input[0].set_value(50000)
    # pick the main JE uploader by label (sidebar adds mapping + CoA uploaders too)
    up = [u for u in at.file_uploader if "journal-entry" in (u.label or "").lower()][0]
    up.set_value(("sample_je.csv", SAMPLE.read_bytes(), "text/csv"))
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
    assert metrics.get("Surfaced for review") == "15"
    res = at.session_state["jr_result"]
    assert len(res.entries) == 15
    assert res.entries.iloc[0]["je_id"] == "JE-2024-0155"
    labels = [d.label for d in at.get("download_button")]
    assert any("Excel" in l for l in labels) and any("CSV" in l for l in labels)


if __name__ == "__main__":
    for fn in [test_ui_runs_without_exception, test_ui_renders_expected_outputs]:
        fn(); print(f"PASS  {fn.__name__}")
    print("UI smoke test passed.")
