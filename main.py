from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from src.bootstrap import bootstrap, load_dotenv
from src.runtime.app import Runtime
from src.storage.housekeeping import prune_old_processed_events

os.environ["TZ"] = "UTC"
time.tzset()

logging.basicConfig(
    level=getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("main")

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("AGENT_HOME", ROOT)).resolve()
os.environ.setdefault("AGENT_HOME", str(DATA_ROOT))


def main() -> None:
    env_file = DATA_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    load_dotenv(ROOT / ".env")

    if not env_file.exists():
        # Self-hosted / container deployments pass configuration as environment
        # variables and ship no .env inside the data root. Fail only when the
        # essentials are missing, instead of assuming an unmounted LUKS volume.
        missing = [k for k in ("OPENROUTER_KEY", "EMAIL_USER", "EMAIL_PASSWORD") if not os.environ.get(k)]
        if missing:
            logger.critical(
                "no %s and missing env vars: %s — nothing to run", env_file, ", ".join(missing)
            )
            return
        logger.info("no %s — running from environment variables", env_file)

    bootstrap(DATA_ROOT, ROOT)
    prune_old_processed_events(DATA_ROOT)

    rt = Runtime.build(DATA_ROOT, ROOT)
    rt.log_summary()
    rt.start_background()
    rt.run()


if __name__ == "__main__":
    main()
