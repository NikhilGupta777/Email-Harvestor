# email_harvester/utils/helpers.py

from urllib.parse import urlparse, urlunparse, urljoin
import logging

logger = logging.getLogger(__name__)

def normalize_url(url: str, base_url: str = None) -> str | None:
    """
    Normalizes a URL: resolves relative paths, ensures scheme, removes fragment.
    Returns None if the URL scheme is not http or https.
    """
    try:
        if base_url:
            # Handle potential whitespace in hrefs
            url = urljoin(base_url, url.strip())
        else:
            url = url.strip()

        parsed = urlparse(url)

        # Ensure scheme is present (default to https if ambiguous relative to base)
        scheme = parsed.scheme.lower()
        if not scheme and base_url:
             base_parsed = urlparse(base_url)
             scheme = base_parsed.scheme.lower()
        elif not scheme:
             # Cannot determine scheme without base, maybe default or skip?
             # Let's assume https if it looks like a domain path
             if parsed.netloc:
                 scheme = 'https'
             else:
                 # If no netloc and no scheme, it's likely an invalid relative URL without base
                 logger.debug(f"Cannot normalize URL without scheme and base: {url}")
                 return None

        if scheme not in ('http', 'https'):
            logger.debug(f"Skipping non-http(s) URL: {url}")
            return None

        # Rebuild the URL without fragment and lowercase scheme/netloc
        # Keep path, query, etc., case-sensitive as they can be
        normalized = urlunparse((
            scheme,
            parsed.netloc.lower(),
            parsed.path if parsed.path else '/', # Ensure path starts with / if empty
            parsed.params,
            parsed.query,
            '' # Remove fragment
        ))

        # Optional: Add trailing slash for consistency? Depends on server behavior. Let's omit for now.
        # if not parsed.path.endswith('/') and not parsed.params and not parsed.query and not '.' in parsed.path.split('/')[-1]:
        #      normalized += '/'


        return normalized

    except ValueError as e:
        # Catch specific errors like invalid IPv6 addresses etc. during parsing
        logger.warning(f"Value error normalizing URL '{url}' (base: {base_url}): {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error normalizing URL '{url}' (base: {base_url}): {e}")
        return None


def get_domain(url: str) -> str | None:
    """Extracts the network location (domain) from a URL."""
    try:
        parsed = urlparse(url)
        # Handle cases like 'mailto:', 'javascript:' which have no netloc
        if not parsed.netloc:
             return None
        return parsed.netloc.lower()
    except Exception as e:
        logger.error(f"Error parsing domain from URL '{url}': {e}")
        return None

def is_same_domain(url1: str, url2: str, scope: str = 'domain') -> bool:
    """
    Checks if two URLs belong to the same domain or subdomain based on scope.
    Scope can be 'domain' (e.g., example.com) or 'subdomain' (e.g., www.example.com).
    """
    domain1 = get_domain(url1)
    domain2 = get_domain(url2)

    if not domain1 or not domain2:
        return False

    if scope == 'subdomain':
        return domain1 == domain2
    elif scope == 'domain':
        # Compare the base domain (e.g., example.com from www.example.com)
        # This is a simplified check; TLD libraries (like tldextract) are more robust
        # but add external dependency.
        domain1_parts = domain1.split('.')
        domain2_parts = domain2.split('.')
        # Basic check: compare last two parts (handles .com, .co.uk etc. simply)
        # Need at least 2 parts for this logic (e.g., example.com)
        if len(domain1_parts) >= 2 and len(domain2_parts) >= 2:
             base_domain1 = '.'.join(domain1_parts[-2:])
             base_domain2 = '.'.join(domain2_parts[-2:])
             return base_domain1 == base_domain2
        else:
             # Handle cases like 'localhost' or single-part domains/IPs
             return domain1 == domain2
    else:
        logger.warning(f"Invalid scope '{scope}' provided to is_same_domain.")
        return False

