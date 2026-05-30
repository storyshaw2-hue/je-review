"""
build_web.py — Bundle the jereview package into a single static HTML page that
runs entirely in the browser via stlite (Streamlit on WebAssembly/Pyodide).

The page processes uploaded ledgers in the visitor's browser — no server, no
upload, no data egress. Host the resulting web/index.html on GitHub Pages.

Run:  python scripts/build_web.py
Test locally (must be served over http, not file://):
      python -m http.server 8000   →   open http://localhost:8000
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "jereview"
OUT = ROOT  # index.html at repo root → GitHub Pages "deploy from branch / root" just works

# Runtime modules needed in the browser (CLI / __main__ are not).
MODULES = ["__init__", "schema", "rules", "review", "engine", "report", "triage",
           "pipeline", "webapp"]

STLITE_VERSION = "0.77.0"

ENTRYPOINT = (
    "from jereview.webapp import render\n"
    "render(allow_ai=False)  # in-browser build: no network egress\n"
)

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>JE Risk Review — runs in your browser</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{ver}/build/style.css" />
<style>
  #loading {{ font-family: Arial, Helvetica, sans-serif; max-width: 640px;
              margin: 12vh auto; padding: 0 1.5rem; color: #1F3864; line-height: 1.5; }}
  #loading h1 {{ font-size: 1.4rem; }}
  #loading .muted {{ color: #595959; font-size: .9rem; }}
</style>
</head>
<body>
<div id="root">
  <div id="loading">
    <h1>🧾 Journal Entry Risk Review</h1>
    <p>Loading the tool… the first load downloads a Python runtime into your browser,
       which can take 30–60 seconds. It is cached after that.</p>
    <p class="muted">Everything runs in your browser. Your ledger is never uploaded —
       it does not leave this computer.</p>
  </div>
</div>
<script type="module">
import {{ mount }} from "https://cdn.jsdelivr.net/npm/@stlite/browser@{ver}/build/stlite.js";
const files = {files};
mount(
  {{ requirements: ["pandas", "openpyxl"], entrypoint: "app.py", files }},
  document.getElementById("root"),
);
</script>
</body>
</html>
"""


def main() -> None:
    files: dict[str, str] = {"app.py": ENTRYPOINT}
    for m in MODULES:
        files[f"jereview/{m}.py"] = (PKG / f"{m}.py").read_text(encoding="utf-8")

    # Embed each file as a JSON string literal (valid JS, correct escaping).
    items = ",\n  ".join(f"{json.dumps(name)}: {json.dumps(content)}"
                         for name, content in files.items())
    files_js = "{\n  " + items + "\n}"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(HTML.format(ver=STLITE_VERSION, files=files_js),
                                    encoding="utf-8")
    total = sum(len(c) for c in files.values())
    print(f"Wrote {OUT / 'index.html'}  ({len(files)} python files, {total:,} chars embedded)")


if __name__ == "__main__":
    main()
