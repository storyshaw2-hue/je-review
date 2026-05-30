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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E🧾%3C/text%3E%3C/svg%3E" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@stlite/browser@{ver}/build/style.css" />
<style>
  html, body {{ margin: 0; padding: 0; background: #F5F7FB; min-height: 100vh; }}
  #boot-loader {{
    position: fixed; inset: 0; display: flex; align-items: center; justify-content: center;
    background: #F5F7FB; z-index: 99999;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    color: #1F3864; transition: opacity .4s ease;
  }}
  #boot-loader .box {{ max-width: 520px; padding: 0 1.5rem; text-align: center; }}
  #boot-loader h1 {{ font-size: 1.5rem; margin: 0 0 .75rem; font-weight: 600; }}
  #boot-loader p {{ margin: .5rem 0; line-height: 1.5; }}
  #boot-loader .muted {{ color: #595959; font-size: .9rem; }}
  #boot-loader .spinner {{
    width: 36px; height: 36px; margin: 0 auto 1.25rem;
    border: 3px solid #D6DEEE; border-top-color: #1F3864;
    border-radius: 50%; animation: spin 0.9s linear infinite;
  }}
  #boot-loader .progress-wrap {{
    margin: 1.25rem auto 0; width: 320px; max-width: 80%;
  }}
  #boot-loader .progress-bar {{
    width: 100%; height: 6px; background: #D6DEEE; border-radius: 3px; overflow: hidden;
  }}
  #boot-loader .progress-fill {{
    height: 100%; width: 0%; background: linear-gradient(90deg, #1F3864 0%, #2E5BA6 100%);
    border-radius: 3px; transition: width .4s ease;
  }}
  #boot-loader .progress-label {{
    margin-top: .5rem; font-size: .8rem; color: #777;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    display: flex; justify-content: space-between;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  /* Mobile: tighten paddings, smaller fonts, stack footer */
  @media (max-width: 640px) {{
    .block-container {{ padding-left: 0.75rem !important; padding-right: 0.75rem !important; }}
    .je-footer {{ flex-direction: column !important; gap: 2px; padding: 6px 10px !important; font-size: 11px !important; }}
    #boot-loader h1 {{ font-size: 1.2rem; }}
    #boot-loader .progress-wrap {{ width: 90%; }}
  }}
  #loading {{ font-family: Arial, Helvetica, sans-serif; max-width: 640px;
              margin: 12vh auto; padding: 0 1.5rem; color: #1F3864; line-height: 1.5; }}
  #loading h1 {{ font-size: 1.4rem; }}
  #loading .muted {{ color: #595959; font-size: .9rem; }}
</style>
</head>
<body>
<div id="boot-loader">
  <div class="box">
    <div class="spinner"></div>
    <h1>🧾 Journal Entry Risk Review</h1>
    <p>Loading the tool…</p>
    <p class="muted">The first load downloads a Python runtime into your browser
       (~15–60 seconds). It is cached after that, so future loads are instant.</p>
    <p class="muted">Everything runs in your browser. Your ledger is never uploaded —
       it does not leave this computer.</p>
    <div class="progress-wrap">
      <div class="progress-bar"><div class="progress-fill" id="boot-fill"></div></div>
      <div class="progress-label">
        <span id="boot-status">Starting…</span>
        <span id="boot-pct">0%</span>
      </div>
    </div>
  </div>
</div>
<div id="root">
  <div id="loading">
    <h1>🧾 Journal Entry Risk Review</h1>
    <p>Loading the tool… the first load downloads a Python runtime into your browser,
       which can take 30–60 seconds. It is cached after that.</p>
    <p class="muted">Everything runs in your browser. Your ledger is never uploaded —
       it does not leave this computer.</p>
  </div>
</div>
<script>
  // Hide the boot loader once the Streamlit app actually renders.
  (function () {{
    var status = document.getElementById('boot-status');
    var fill = document.getElementById('boot-fill');
    var pct = document.getElementById('boot-pct');
    var steps = ['Starting…', 'Downloading Python runtime…', 'Installing packages…', 'Almost there…'];
    var i = 0;
    var statusTimer = setInterval(function () {{
      i = Math.min(i + 1, steps.length - 1);
      if (status) status.textContent = steps[i];
    }}, 4000);
    // Animated progress: smooth climb to 90% over ~25s, then idles at 90% until mount detected.
    // Curves toward 90 asymptotically so it never "finishes" before stlite actually mounts.
    var startTime = Date.now();
    var progressTimer = setInterval(function () {{
      var elapsed = (Date.now() - startTime) / 1000;
      var target = Math.min(90, 90 * (1 - Math.exp(-elapsed / 12)));
      if (fill) fill.style.width = target.toFixed(1) + '%';
      if (pct) pct.textContent = Math.round(target) + '%';
    }}, 200);
    var obs = new MutationObserver(function () {{
      var mounted = document.querySelector('[data-testid="stAppViewContainer"]') ||
                    document.querySelector('.stApp') ||
                    document.querySelector('iframe[title="streamlit"]');
      if (mounted) {{
        // Snap to 100% briefly so users see completion before fade.
        if (fill) fill.style.width = '100%';
        if (pct) pct.textContent = '100%';
        if (status) status.textContent = 'Ready';
        var bl = document.getElementById('boot-loader');
        if (bl) {{
          setTimeout(function () {{ bl.style.opacity = '0'; }}, 200);
          setTimeout(function () {{ bl.remove(); }}, 650);
        }}
        clearInterval(statusTimer);
        clearInterval(progressTimer);
        obs.disconnect();
      }}
    }});
    obs.observe(document.body, {{ childList: true, subtree: true }});
    // Safety net: force-hide after 90s no matter what.
    setTimeout(function () {{
      var bl = document.getElementById('boot-loader');
      if (bl) {{ bl.style.opacity = '0'; setTimeout(function(){{ bl.remove(); }}, 450); }}
      clearInterval(statusTimer);
      clearInterval(progressTimer);
      obs.disconnect();
    }}, 90000);
  }})();
</script>
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

    # Bundle sample data so the in-browser "Load sample" button works.
    sample_dir = ROOT / "sample_data"
    for sample_file in ["sample_je_errors.csv", "sample_coa.csv"]:
        sample_path = sample_dir / sample_file
        if sample_path.exists():
            files[f"sample_data/{sample_file}"] = sample_path.read_text(encoding="utf-8")

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
