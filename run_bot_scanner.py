# run_bot_scanner.py
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.services.background_scanner import BackgroundScannerDaemon

if __name__ == "__main__":
    daemon = BackgroundScannerDaemon()
    daemon.start(scan_minutes=10, closing_minutes=5, settlement_minutes=30)