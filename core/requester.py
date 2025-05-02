# email_harvester/core/requester.py

import asyncio
import aiohttp
import logging
from urllib.parse import urlparse
# Using standard library robotparser - needs to be run in executor for async
from urllib.robotparser import RobotFileParser
from concurrent.futures import ThreadPoolExecutor

# Use relative imports within the package
from utils import config, helpers

logger = logging.getLogger(__name__)

# Executor for running synchronous robotparser code
# Adjust max_workers based on expected load/CPU cores
executor = ThreadPoolExecutor(max_workers=5)

class Requester:
    def __init__(self, user_agent=config.DEFAULT_USER_AGENT, rate_limit=config.DEFAULT_RATE_LIMIT, timeout=config.DEFAULT_TIMEOUT):
        self.user_agent = user_agent
        # Calculate delay from rate limit
        self.request_delay = 1.0 / rate_limit if rate_limit > 0 else 0
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.robot_parsers = {} # Cache for RobotFileParser objects per domain {domain: parser_instance}
        self.robot_parsers_lock = asyncio.Lock() # Lock for accessing the cache
        logger.info(f"Requester initialized. Rate limit: {rate_limit} req/s (delay: {self.request_delay:.2f}s), Timeout: {timeout}s")
        # Note: aiohttp session should ideally be created and closed externally or managed carefully

    async def _get_robot_parser(self, session: aiohttp.ClientSession, target_domain: str, target_scheme: str) -> RobotFileParser | None:
        """Fetches and parses robots.txt for a domain asynchronously, caching the result."""
        async with self.robot_parsers_lock:
            if target_domain in self.robot_parsers:
                return self.robot_parsers[target_domain]

        # If not cached, fetch and parse
        robots_url = f"{target_scheme}://{target_domain}/robots.txt"
        logger.info(f"Fetching robots.txt from: {robots_url}")
        parser = RobotFileParser()
        parser.set_url(robots_url) # Set URL for the parser

        try:
            # Fetch robots.txt content
            async with session.get(robots_url, timeout=self.timeout) as response:
                 if response.status == 200:
                     robots_content = await response.text(encoding='utf-8', errors='ignore')
                     # Parse the content using standard library parser in an executor thread
                     loop = asyncio.get_running_loop()
                     await loop.run_in_executor(executor, parser.parse, robots_content.splitlines())
                     logger.info(f"Successfully fetched and parsed robots.txt for {target_domain}")
                 elif response.status in [401, 403, 404]:
                      # If robots.txt is missing or forbidden, assume crawl is allowed
                      # but log the situation. Some interpretations disallow on 401/403.
                      # Let's assume allowed if not explicitly disallowed by a 200 OK file.
                      logger.info(f"robots.txt for {target_domain} status {response.status}. Assuming allowed.")
                      # We still cache 'None' to indicate we tried and failed/it wasn't found
                      parser = None # Indicate fetch attempt failed or file not found/applicable
                 else:
                      logger.warning(f"robots.txt for {target_domain} returned unexpected status {response.status}. Assuming allowed.")
                      parser = None

        except asyncio.TimeoutError:
             logger.warning(f"Timeout fetching robots.txt for {target_domain}. Assuming allowed.")
             parser = None
        except aiohttp.ClientError as e:
            logger.warning(f"HTTP error fetching robots.txt for {target_domain}: {e}. Assuming allowed.")
            parser = None
        except Exception as e:
            # Includes parsing errors within the executor
            logger.error(f"Unexpected error fetching/parsing robots.txt for {target_domain}: {e}. Assuming allowed.")
            parser = None

        # Cache the result (parser instance or None)
        async with self.robot_parsers_lock:
            self.robot_parsers[target_domain] = parser
        return parser


    async def fetch(self, url: str, session: aiohttp.ClientSession) -> tuple[str | None, str | None]:
        """
        Fetches a URL asynchronously respecting robots.txt and rate limits.
        Returns (html_content, final_url) or (None, final_url_or_original) on failure/disallow.
        """
        # Apply rate limit delay BEFORE request
        if self.request_delay > 0:
            await asyncio.sleep(self.request_delay)

        parsed_url = urlparse(url)
        domain = helpers.get_domain(url)
        scheme = parsed_url.scheme

        if not domain or not scheme:
            logger.warning(f"Cannot fetch URL without domain/scheme: {url}")
            return None, url

        # --- robots.txt check ---
        robot_parser = await self._get_robot_parser(session, domain, scheme)
        if robot_parser is not None: # If None, means we assume allowed
             # Use the standard library can_fetch method
             loop = asyncio.get_running_loop()
             try:
                 is_allowed = await loop.run_in_executor(executor, robot_parser.can_fetch, self.user_agent, url)
                 if not is_allowed:
                     logger.info(f"Skipping disallowed URL by robots.txt: {url}")
                     return None, url # Return original URL as final_url in this case
             except Exception as e:
                  logger.error(f"Error checking robots.txt permission for {url}: {e}. Assuming allowed.")
        # --- End robots.txt check ---


        logger.debug(f"Fetching URL: {url}")
        headers = {'User-Agent': self.user_agent, 'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9'}
        final_url_on_error = url # Keep track of original url for error returns
        try:
            async with session.get(url, headers=headers, timeout=self.timeout, allow_redirects=True) as response:
                final_url = str(response.url) # Get URL after redirects
                final_url_on_error = final_url # Update url to return on error
                logger.debug(f"Got response {response.status} for {url} (final: {final_url})")

                # Raise exception for 4xx/5xx errors AFTER getting final URL
                response.raise_for_status()

                # Check content type - only process HTML
                content_type = response.headers.get('Content-Type', '').lower()
                if 'text/html' not in content_type:
                    logger.debug(f"Skipping non-HTML content type '{content_type}' for URL: {final_url}")
                    return None, final_url # Return final URL even if content skipped

                # Read content (use cchardet if available via aiohttp)
                # Specify utf-8 primarily, ignore errors for resilience
                html_content = await response.text(encoding='utf-8', errors='ignore')
                return html_content, final_url

        except asyncio.TimeoutError:
            logger.warning(f"Timeout fetching URL: {url}")
            return None, final_url_on_error
        except aiohttp.ClientResponseError as e:
             logger.warning(f"HTTP error {e.status} for URL {url} (final: {final_url_on_error}): {e.message}")
             return None, final_url_on_error
        except aiohttp.ClientError as e:
            # More general client errors (connection issues, DNS errors etc.)
            logger.error(f"Client error fetching URL {url}: {e}")
            return None, final_url_on_error
        except Exception as e:
            logger.error(f"Unexpected error fetching URL {url}: {e}", exc_info=False) # Set exc_info=True for traceback
            return None, final_url_on_error

