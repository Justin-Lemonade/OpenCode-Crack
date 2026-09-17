"""Central configuration for the orchestration framework.

Deliberately minimal: these are the ONLY six constants that anything in
this package imports from a config module (confirmed by grep across
orchestrator/, runtime/, tools/, prompts/, storage/migrations.py during
the C-076 extraction audit). This is NOT a copy of AI-Brain's
src/config.py — that file also holds TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY,
and other domain/provider config this package has no business knowing
about.

NOTE for whoever finalizes the repo layout: _PROJECT_ROOT assumes this
file sits one level below the repo root (e.g. `agent_orchestrator/config.py`
with data/ and .swarm/ as repo-root-level directories). Adjust the
.parent chain if the final package layout differs (e.g. src-layout).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONTROL_DB_PATH = Path(os.getenv("CONTROL_DB_PATH", str(_PROJECT_ROOT / "data" / "control.db")))

OPENCODE_BASE_URL = os.getenv("OPENCODE_BASE_URL", "http://localhost:4096")

SWARM_DB_PATH = Path(os.getenv("SWARM_DB_PATH", str(_PROJECT_ROOT / ".swarm" / "swarm.db")))

SWARM_SMOKE_MODEL = os.getenv("SWARM_SMOKE_MODEL", "anthropic:claude-haiku-4-5")
SWARM_SMOKE_TIMEOUT = int(os.getenv("SWARM_SMOKE_TIMEOUT", "600"))
