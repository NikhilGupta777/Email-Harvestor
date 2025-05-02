# email_harvester/core/crawler.py

import asyncio
import aiohttp
import logging
from urllib.parse import urlparse

# Use relative imports within the package
from core.requester import Requester
from core.parser import Parser
from core.storage import EmailStorage
from utils import helpers, config

logger = logging.getLogger(__name__)

class Crawler:
    def __init__(self, start_url: str, storage: EmailStorage, max_depth: int = config.DEFAULT_MAX_DEPTH,
                 max_pages: int = config.DEFAULT_MAX_PAGES, rate_limit: float = config.DEFAULT_RATE_LIMIT,
                 scope: str = config.DEFAULT_CRAWL_SCOPE, user_agent: str = config.DEFAULT_USER_AGENT,
                 concurrency: int = 10): # Number of parallel workers

        self.start_url = helpers.normalize_url(start_url)
        if not self.start_url:
             raise ValueError("Invalid starting URL provided.")

        self.allowed_domain_info = urlparse(self.start_url) # Store parsed start URL info
        self.scope = scope # 'domain' or 'subdomain'
        logger.info(f"Crawler scope set to '{self.scope}' for base URL '{self.start_url}' (Domain: {self.allowed_domain_info.netloc})")

        self.queue = asyncio.Queue()
        self.visited_urls = set() # Store normalized URLs
        self.crawled_count = 0
        self.max_depth = max_depth
        self.max_pages = max_pages
        self._stop_requested = False # Flag to signal workers to stop early

        self.storage = storage
        self.requester = Requester(user_agent=user_agent, rate_limit=rate_limit)
        self.parser = Parser()

        self.concurrency = max(1, concurrency) # Ensure at least 1 worker
        self.active_workers = 0 # Track active workers for graceful shutdown
        self._workers_finished_event = asyncio.Event()


    async def _worker(self, session: aiohttp.ClientSession):
        """Worker task that processes URLs from the queue."""
        self.active_workers += 1
        logger.debug(f"Worker started. Active workers: {self.active_workers}")
        try:
            while not self._stop_requested:
                try:
                    # Wait for an item from the queue, with a timeout to allow checking stop flag
                    current_url, current_depth = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    # No item in queue for a while, check if we should stop
                    if self.queue.empty() and self.active_workers <= 1: # Check if likely the last worker
                         logger.debug("Worker queue timeout and likely last worker, potentially finishing.")
                    continue # Continue loop to check stop flag or wait again
                except asyncio.CancelledError:
                    logger.info("Worker cancelled during queue get.")
                    break


                task_start_time = asyncio.get_event_loop().time()
                logger.debug(f"Worker processing: {current_url} (Depth: {current_depth})")

                # --- Pre-fetch Checks ---
                if current_url in self.visited_urls:
                    logger.debug(f"Already visited: {current_url}")
                    self.queue.task_done()
                    continue

                if current_depth > self.max_depth:
                    logger.debug(f"Max depth {self.max_depth} reached for: {current_url}")
                    self.visited_urls.add(current_url) # Mark as visited to avoid re-queueing
                    self.queue.task_done()
                    continue

                if self.crawled_count >= self.max_pages:
                    logger.info(f"Max page limit ({self.max_pages}) reached. Worker stopping fetch.")
                    self._stop_requested = True # Signal other workers
                    self.visited_urls.add(current_url) # Still mark as visited if retrieved from queue
                    self.queue.task_done()
                    break # Stop this worker's loop

                # --- Mark as visited BEFORE processing ---
                self.visited_urls.add(current_url)
                self.crawled_count += 1
                page_num = self.crawled_count # Capture count before async fetch
                logger.info(f"Crawling #{page_num}/{self.max_pages}: {current_url} (Depth: {current_depth})")

                # --- Fetch and Process ---
                html_content, final_url = await self.requester.fetch(current_url, session)
                base_url_for_parsing = current_url # Default to original URL

                # Handle redirects: mark final URL visited, check scope again
                if final_url and final_url != current_url:
                    logger.info(f"Redirected: {current_url} -> {final_url}")
                    if final_url in self.visited_urls:
                        logger.debug(f"Redirected URL already visited: {final_url}")
                        self.queue.task_done()
                        continue
                    # Check if redirected URL is still within scope
                    if not helpers.is_same_domain(self.start_url, final_url, self.scope):
                         logger.info(f"Redirected URL out of scope: {final_url}")
                         self.visited_urls.add(final_url) # Mark visited to prevent loops
                         self.queue.task_done()
                         continue

                    self.visited_urls.add(final_url)
                    logger.debug(f"Added redirected URL to visited: {final_url}")
                    base_url_for_parsing = final_url # Use final URL for parsing relative links


                if html_content:
                    # Parse content for emails and links
                    emails, links = self.parser.parse_page(html_content, base_url_for_parsing)

                    # Store found emails
                    if emails:
                        # --- CORRECTED LINE: Removed await ---
                        self.storage.add_emails(emails) # Call synchronous method

                    # Add new, valid links to the queue
                    next_depth = current_depth + 1
                    if next_depth <= self.max_depth and not self._stop_requested:
                        added_count = 0
                        links_to_add = []
                        for link in links:
                            # Check visited status again *before* putting in queue
                            if link not in self.visited_urls:
                                # Check scope before adding
                                if helpers.is_same_domain(self.start_url, link, self.scope):
                                    # Add to a temporary list first to avoid awaiting put in loop
                                    links_to_add.append((link, next_depth))
                                else:
                                    logger.debug(f"Link out of scope: {link}")

                        # Add valid links to the queue
                        if links_to_add:
                             logger.debug(f"Adding {len(links_to_add)} new links to queue from {base_url_for_parsing}")
                             for link_info in links_to_add:
                                 # Check stop flag again before putting
                                 if self._stop_requested: break
                                 await self.queue.put(link_info)


                # Mark task as done in the queue
                self.queue.task_done()
                task_duration = asyncio.get_event_loop().time() - task_start_time
                logger.debug(f"Finished processing {current_url} in {task_duration:.2f}s. Queue size: {self.queue.qsize()}")


        except asyncio.CancelledError:
            logger.info("Worker stopping due to cancellation.")
        except Exception as e:
            # Log exceptions from fetching/parsing if they bubble up
            # Use logger.exception to include traceback automatically
            logger.exception(f"Error in worker processing URL '{current_url}': {e}")
            # Ensure task_done is called if an item was potentially retrieved before error
            try:
                 self.queue.task_done() # Attempt to mark done, may raise if already done
            except ValueError: # task_done() called too many times.
                 pass # Ignore error if task_done called too many times
            except RuntimeError: # Cannot call task_done() on a RuntimeError loop?
                 pass # Ignore if loop is shutting down
            # Continue processing next item if possible
            # Depending on the error, might want to break or implement backoff/retry
        finally:
            self.active_workers -= 1
            logger.debug(f"Worker finished. Active workers: {self.active_workers}")
            if self.active_workers == 0:
                 logger.info("All workers have finished.")
                 self._workers_finished_event.set() # Signal that all workers are done


    async def crawl(self):
        """Starts the crawling process and manages workers."""
        logger.info(f"Starting crawl from: {self.start_url} with {self.concurrency} workers.")
        await self.queue.put((self.start_url, 0)) # Add start URL with depth 0
        self.visited_urls.clear() # Ensure visited set is clear at start
        self.crawled_count = 0
        self._stop_requested = False
        self._workers_finished_event.clear()

        start_time = asyncio.get_event_loop().time()

        # Create a single session for efficiency
        # Moved session creation outside try block to ensure it's closed if init fails
        connector = aiohttp.TCPConnector(limit=self.concurrency) # Limit total connections
        async with aiohttp.ClientSession(connector=connector) as session:
            # Create worker tasks
            workers = [asyncio.create_task(self._worker(session)) for _ in range(self.concurrency)]

            # Wait for the queue to be processed OR stop signal
            while True:
                 all_tasks_done = self.queue.empty()
                 # Check if queue is empty and workers might be idle - .join() alternative
                 if all_tasks_done and self.active_workers > 0:
                      # Give workers a chance to finish or fetch new items if queue refills
                      await asyncio.sleep(0.5)
                      all_tasks_done = self.queue.empty() # Recheck queue

                 # Check if all workers have signaled they are finished
                 if self._workers_finished_event.is_set():
                      logger.info("All workers signaled finished.")
                      break

                 # Check stop flag (e.g., max pages)
                 if self._stop_requested:
                      logger.info("Stop requested (e.g., max pages reached). Waiting for workers to finish current tasks.")
                      # Wait for workers to finish naturally or add a timeout?
                      try:
                           await asyncio.wait_for(self._workers_finished_event.wait(), timeout=10.0) # Wait for signal
                      except asyncio.TimeoutError:
                           logger.warning("Timeout waiting for workers to finish after stop request.")
                      break # Exit main loop after waiting or timeout

                 await asyncio.sleep(0.1) # Prevent busy-waiting


            logger.info("Main crawl loop finished. Cancelling any remaining worker tasks...")

            # Cancel any workers that might still be running (e.g., waiting on queue)
            for worker in workers:
                if not worker.done():
                     worker.cancel()

            # Wait for workers to finish cancelling
            await asyncio.gather(*workers, return_exceptions=True) # Allow exceptions during cancellation

            crawl_duration = asyncio.get_event_loop().time() - start_time
            logger.info(f"Crawl complete. Duration: {crawl_duration:.2f}s.")
            logger.info(f"Total pages visited: {len(self.visited_urls)}. Crawl attempts: {self.crawled_count}.")
            # Get final count synchronously now
            final_count = self.storage.count()
            logger.info(f"Total unique emails found: {final_count}")

