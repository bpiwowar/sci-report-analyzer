"""Application paths and constants.

The data directory (database, PDFs) is, in order of precedence: the
``--data-dir`` option, the ``SCI_REPORT_ANALYZER_DATA`` environment variable, the location saved
from Settings → Data (in ``LOCATION_FILE``), or ``DEFAULT_DATA_DIR``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DB_NAME = "sci-report-analyzer.sqlite"

DEFAULT_DATA_DIR = Path.home() / ".local/share/sci-report-analyzer"
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
# Where the data directory chosen in the settings is remembered (it can't live inside it).
LOCATION_FILE = _CONFIG_HOME / "sci-report-analyzer" / "location.json"


def saved_data_dir() -> Path | None:
    try:
        return Path(json.loads(LOCATION_FILE.read_text())["data_dir"]).expanduser()
    except (OSError, KeyError, ValueError, TypeError):
        return None


def save_data_dir(path: Path | None) -> None:
    """Remember the data directory for the next start (None: back to the default)."""
    if path is None:
        LOCATION_FILE.unlink(missing_ok=True)
        return
    LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATION_FILE.write_text(json.dumps({"data_dir": str(path)}))


def _initial_data_dir() -> tuple[Path, str]:
    if env := os.environ.get("SCI_REPORT_ANALYZER_DATA"):
        return Path(env).expanduser(), "the SCI_REPORT_ANALYZER_DATA environment variable"
    if saved := saved_data_dir():
        return saved, "the settings"
    return DEFAULT_DATA_DIR, "the default"


DATA_DIR, DATA_DIR_ORIGIN = _initial_data_dir()
DB_PATH = DATA_DIR / DB_NAME


def set_data_dir(path: Path, origin: str = "the --data-dir option") -> None:
    """Use another data directory (before the database is opened)."""
    global DATA_DIR, DATA_DIR_ORIGIN, DB_PATH
    DATA_DIR = path.expanduser().resolve()
    DATA_DIR_ORIGIN = origin
    DB_PATH = DATA_DIR / DB_NAME


def db_url() -> str:
    return os.environ.get("SCI_REPORT_ANALYZER_DB_URL", f"sqlite:///{DB_PATH}")
