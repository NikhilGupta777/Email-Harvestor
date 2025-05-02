# email_harvester/utils/logging_setup.py

import logging
import sys
# Use relative import within the package
from . import config

def setup_logging(level=config.DEFAULT_LOG_LEVEL):
    """Configures the root logger."""
    logger = logging.getLogger() # Get root logger
    logger.setLevel(level)

    # Prevent adding multiple handlers if called again
    if not logger.handlers:
        # Console Handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        formatter = logging.Formatter(config.LOG_FORMAT, datefmt=config.LOG_DATE_FORMAT)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # Optional: File Handler (uncomment to enable logging to file)
        # try:
        #     # Ensure log file path is relative to execution or absolute
        #     # For simplicity, log in the current working directory if enabled
        #     fh = logging.FileHandler("crawler.log", mode='a') # Append mode
        #     fh.setLevel(level)
        #     fh.setFormatter(formatter)
        #     logger.addHandler(fh)
        # except Exception as e:
        #     logging.error(f"Failed to set up file logging: {e}")

    # Set levels for libraries that can be noisy
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Use logger instance after basicConfig/handler setup
    log_instance = logging.getLogger(__name__) # Get logger for this module
    log_instance.info("Logging configured.")

