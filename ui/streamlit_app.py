"""
Native Streamlit entry point (local install).

    streamlit run ui/streamlit_app.py

Runs locally; the AI-triage option is available here. The in-browser build uses
the same renderer with allow_ai=False (see web/).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jereview.webapp import render  # noqa: E402

render(allow_ai=True)
