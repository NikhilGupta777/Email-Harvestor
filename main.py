# email_harvester/main.py

import asyncio
import argparse
import logging
import time
import sys

# Adjust path for relative imports if running script directly
# This is often better handled by installing the package or using python -m email_harvester.main
try:
    from utils import config, logging_setup, helpers
    from core.crawler import Crawler
    from core.storage import EmailStorage
except ImportError:
     # Simple fallback if running script directly from project root
     import os
     sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
     from utils import config, logging_setup, helpers
     from core.crawler import Crawler
     from core.storage import EmailStorage


# Set up logging as early as possible
logging_setup.setup_logging() # Call setup function
logger = logging.getLogger(__name__) # Get logger for this module


def main():
    parser = argparse.ArgumentParser(
        description="Email Harvester - Crawls a website to find email addresses.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show defaults in help
    )

    parser.add_argument("start_url", help="The starting URL to crawl (e.g., https://example.com)")
    parser.add_argument("-o", "--output", default=config.DEFAULT_OUTPUT_FILENAME,
                        help="Output file for found emails")
    parser.add_argument("-d", "--depth", type=int, default=config.DEFAULT_MAX_DEPTH,
                        help="Maximum crawl depth")
    parser.add_argument("-p", "--max-pages", type=int, default=config.DEFAULT_MAX_PAGES,
                        help="Maximum number of pages to crawl")
    parser.add_argument("-r", "--rate", type=float, default=config.DEFAULT_RATE_LIMIT,
                        help="Maximum requests per second (0 for no limit)")
    parser.add_argument("--scope", choices=['domain', 'subdomain'], default=config.DEFAULT_CRAWL_SCOPE,
                        help="Crawling scope: 'domain' limits to base domain, 'subdomain' allows all subdomains")
    parser.add_argument("-ua", "--user-agent", default=config.DEFAULT_USER_AGENT,
                        help="User-Agent string for requests")
    parser.add_argument("--concurrency", type=int, default=10,
                        help="Number of concurrent fetch workers")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose (DEBUG level) logging")

    args = parser.parse_args()

    # Update log level if verbose flag is set AFTER parsing args
    log_level = logging.DEBUG if args.verbose else config.DEFAULT_LOG_LEVEL
    logging_setup.setup_logging(level=log_level) # Re-setup with potentially new level
    if args.verbose: logger.info("Verbose logging enabled.")


    # Validate start URL
    if not args.start_url.startswith(('http://', 'https://')):
         # Attempt to prepend https:// as a common case
         logger.warning(f"Start URL '{args.start_url}' lacks scheme, prepending https://")
         args.start_url = f"https://{args.start_url}"

    normalized_start_url = helpers.normalize_url(args.start_url)
    if not normalized_start_url or not helpers.get_domain(normalized_start_url):
         parser.error(f"Invalid or could not normalize the starting URL: {args.start_url}")


    logger.info("--- Email Harvester Initializing ---")
    logger.info(f"Start URL: {normalized_start_url}")
    logger.info(f"Max Depth: {args.depth}")
    logger.info(f"Max Pages: {args.max_pages}")
    logger.info(f"Rate Limit: {args.rate} req/s")
    logger.info(f"Scope: {args.scope}")
    logger.info(f"Concurrency: {args.concurrency}")
    logger.info(f"Output File: {args.output}")


    start_time = time.monotonic() # Use monotonic clock for duration

    storage = EmailStorage() # Initialize storage
    crawler = Crawler(
        start_url=normalized_start_url,
        storage=storage, # Pass storage instance to crawler
        max_depth=args.depth,
        max_pages=args.max_pages,
        rate_limit=args.rate,
        scope=args.scope,
        user_agent=args.user_agent,
        concurrency=args.concurrency
    )

    final_email_list = [] # Store emails for printing
    try:
        # Run the crawler's main async method
        asyncio.run(crawler.crawl())

    except KeyboardInterrupt:
        logger.warning("Crawling interrupted by user (Ctrl+C). Saving partial results...")
    except ValueError as ve:
         logger.critical(f"Initialization Error: {ve}") # e.g., invalid start URL
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred during crawl: {e}", exc_info=True) # Log traceback
    finally:
        # --- Modified Section ---
        logger.info("Crawl finished or interrupted. Processing results...")
        try:
            # Get emails synchronously
            found_emails = storage.get_emails()
            final_email_list = sorted(list(found_emails)) # Store for printing
            final_email_count = len(final_email_list)

            if final_email_count > 0:
                # Print emails to terminal
                print("\n\n--- Found Emails ---")
                for email in final_email_list:
                    print(email)
                print(f"--- End of {final_email_count} emails ---")

                # Save emails synchronously
                storage.save_to_file(args.output)
            else:
                 logger.info("No emails found to save or print.")

        except Exception as final_e:
             logger.critical(f"Error during final processing/saving: {final_e}", exc_info=True)
        # --- End of Modified Section ---


        end_time = time.monotonic()
        logger.info("--- Email Harvester Finished ---")
        logger.info(f"Total execution time: {end_time - start_time:.2f} seconds")
        # Use the count derived from the final list
        logger.info(f"Total unique emails found: {len(final_email_list)}")


if __name__ == "__main__":
    # Ensure platform-specific asyncio setup if needed (e.g., Windows policies)
    if sys.platform == "win32":
         asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()

