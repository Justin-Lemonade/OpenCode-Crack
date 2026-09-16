"""
Centralized logging setup. Import and call setup_logging() once at
startup (main.py does this); every other module just does
`log = logging.getLogger(__name__)` and logs normally — no per-module
config needed (DRY).
"""
import logging
import uuid
from pathlib import Path

LOG_PATH = Path("data/ai-brain.log")

# Format includes the file/line that logged the message (%(lineno)d) and a
# per-run correlation ID so every line from one invocation can be grouped
# (and cross-referenced with a task ID / session where one is known).
_LOG_FORMAT = (
    "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d "
    "[run=%(run_id)s] %(message)s"
)


def _make_formatter() -> logging.Formatter:
    """One fresh correlation ID per run. The Formatter's `defaults` dict
    (Python 3.10+) supplies it to every record, so we don't need a Filter
    or a custom Logger class."""
    return logging.Formatter(
        _LOG_FORMAT,
        defaults={"run_id": uuid.uuid4().hex[:8]},
    )


def setup_logging(level: int = logging.INFO) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    formatter = _make_formatter()
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(
        level=level,
        handlers=handlers,
    )
