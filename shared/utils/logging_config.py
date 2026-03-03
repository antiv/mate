"""
Configure logging from environment (e.g. LOG_LEVEL=DEBUG).
Call configure_logging() after load_dotenv() in each entry point.
"""

import logging
import os


def configure_logging() -> None:
    """Set root logger level and format from LOG_LEVEL env. Default INFO."""
    level_name = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
