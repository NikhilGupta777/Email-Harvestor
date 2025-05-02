# email_harvester/utils/config.py

import logging

# --- Crawler Settings ---
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36" # A common user agent
)
DEFAULT_RATE_LIMIT = 2.0  # Max requests per second
DEFAULT_MAX_DEPTH = 5     # Default crawl depth limit
DEFAULT_TIMEOUT = 15      # Request timeout in seconds
DEFAULT_MAX_PAGES = 1000  # Default limit on total pages to crawl (to prevent huge crawls)
DEFAULT_CRAWL_SCOPE = 'domain' # 'domain' or 'subdomain'

# --- Output Settings ---
DEFAULT_OUTPUT_FILENAME = "found_emails.txt"

# --- Logging Settings ---
DEFAULT_LOG_LEVEL = logging.INFO
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(module)s:%(lineno)d] - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
