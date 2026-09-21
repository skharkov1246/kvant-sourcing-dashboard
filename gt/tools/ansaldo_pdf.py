#!/usr/bin/env python3
"""HTML → PDF для отчёта Ansaldo (Chromium из /opt/pw-browsers, A4 альбом)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs/ANSALDO-разведка-2026-09.html"
DST = ROOT / "docs/ANSALDO-разведка-2026-09.pdf"
CHROME = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
]


def main():
    exe = next((c for c in CHROME if Path(c).exists()), None)
    if not exe:
        print("Chromium не найден — PDF не собран", file=sys.stderr)
        return 1
    if not SRC.exists():
        print(f"нет {SRC} — сначала gt/tools/ansaldo_report.py", file=sys.stderr)
        return 1
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         f"--print-to-pdf={DST}", SRC.as_uri()],
        check=True, capture_output=True,
    )
    print(f"PDF: {DST.name} {DST.stat().st_size:,} байт")
    return 0


if __name__ == "__main__":
    sys.exit(main())
