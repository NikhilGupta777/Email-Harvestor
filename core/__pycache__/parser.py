# email_harvester/core/parser.py

import logging
from bs4 import BeautifulSoup
# Use relative imports within the package
from core import email_extractor
from utils import helpers

logger = logging.getLogger(__name__)

class Parser:
    def __init__(self):
        logger.info("Parser initialized.")
        # No specific state needed for now

    def parse_page(self, html_content: str, base_url: str) -> tuple[set[str], set[str]]:
        """
        Parses HTML content to extract emails and links.

        Args:
            html_content: The HTML source as a string.
            base_url: The URL from which the content was fetched (for resolving relative links).

        Returns:
            A tuple containing: (set_of_found_emails, set_of_found_links).
        """
        if not html_content or not base_url:
            return set(), set()

        emails_found = set()
        links_found = set()

        try:
            # Use lxml for speed if available, fallback to html.parser
            soup = BeautifulSoup(html_content, 'lxml')
        except ImportError: # Handle case where lxml is not installed
            logger.warning("lxml parser not found, falling back to html.parser (slower).")
            try:
                 soup = BeautifulSoup(html_content, 'html.parser')
            except Exception as e: # Catch potential errors in html.parser too
                 logger.error(f"Failed to parse HTML for {base_url} with any parser: {e}")
                 return set(), set()
        except Exception as e: # Catch other BS4 errors
            logger.error(f"Failed to initialize BeautifulSoup for {base_url}: {e}")
            return set(), set()


        # --- Email Extraction ---
        try:
            # Extract from visible text first
            # Use a generator expression and join for potentially better memory usage on huge pages
            # page_text = ' '.join(soup.stripped_strings) # stripped_strings removes extra whitespace
            # Simpler get_text might be sufficient and less prone to missing things in odd tags
            page_text = soup.get_text(separator=' ')
            emails_from_text = email_extractor.extract_and_clean_emails(page_text)
            emails_found.update(emails_from_text)

            # Extract from mailto links more carefully
            mailto_links = soup.select('a[href^="mailto:"]')
            for link in mailto_links:
                href = link.get('href')
                if href:
                    # mailto:user@example.com?subject=...
                    email_part = href.split(':', 1)[-1].split('?')[0]
                    # Deobfuscate and clean the extracted part
                    cleaned_mailtos = email_extractor.extract_and_clean_emails(email_part)
                    emails_found.update(cleaned_mailtos)

            # (Optional Advanced): Search script tags, meta tags, title etc.
            # Be cautious as this can increase noise significantly.
            # Example: Search meta tags
            # for meta in soup.find_all('meta', attrs={'name': re.compile(r'contact|email', re.I)}):
            #     if meta.get('content'):
            #         emails_found.update(email_extractor.extract_and_clean_emails(meta['content']))

            if emails_found:
                 logger.debug(f"Found {len(emails_found)} potential emails on {base_url}")

        except Exception as e:
            logger.error(f"Error during email extraction on {base_url}: {e}")


        # --- Link Extraction ---
        try:
            for link_tag in soup.find_all('a', href=True):
                href = link_tag['href']
                # Basic filtering of obviously invalid/unwanted hrefs
                if href and not href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'data:')):
                    # Resolve relative URLs and normalize
                    absolute_url = helpers.normalize_url(href, base_url)
                    if absolute_url:
                        links_found.add(absolute_url)

            if links_found:
                 logger.debug(f"Found {len(links_found)} potential links on {base_url}")

        except Exception as e:
            logger.error(f"Error during link extraction on {base_url}: {e}")


        return emails_found, links_found
