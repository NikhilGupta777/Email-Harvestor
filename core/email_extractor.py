# email_harvester/core/email_extractor.py
# (Make sure other imports and code outside this function remain)

import re
import html
import logging
from urllib.parse import unquote

logger = logging.getLogger(__name__)

# --- Robust Email Regex ---
# This pattern aims for a balance between RFC 5322 complexity and practical matching.
# It allows common characters, requires an @, a domain part, and a TLD of 2+ letters.
# It's NOT perfect but better than the initial basic one. Further refinement might be needed.
# Explanation:
# [a-zA-Z0-9._%+-]+       -> Username part: letters, numbers, ._%+-
# @                       -> Literal @ symbol
# [a-zA-Z0-9.-]+          -> Domain name part: letters, numbers, .-
# \.                      -> Literal . symbol separating domain from TLD
# [a-zA-Z]{2,}            -> Top-level domain: 2 or more letters
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


# --- De-obfuscation Patterns ---
# Expanded patterns
OBFUSCATION_PATTERNS = {
    # [at] / [dot] variants
    re.compile(r'\[\s*(at|@)\s*\]', re.IGNORECASE): '@',
    re.compile(r'\[\s*(dot|\.)\s*\]', re.IGNORECASE): '.',
    re.compile(r'\(\s*(at|@)\s*\)', re.IGNORECASE): '@',
    re.compile(r'\(\s*(dot|\.)\s*\)', re.IGNORECASE): '.',
    re.compile(r'\s+(at|@)\s+', re.IGNORECASE): '@',
    re.compile(r'\s+(dot|\.)\s+', re.IGNORECASE): '.',
    # Word replacements
    re.compile(r'\s+AT\s+', re.IGNORECASE): '@',
    re.compile(r'\s+DOT\s+', re.IGNORECASE): '.',
    # Common evasions
    re.compile(r'\s*<span[^>]*>@</span>\s*', re.IGNORECASE): '@', # Handle simple span obfuscation
    re.compile(r'\s*<span[^>]*>\.</span>\s*', re.IGNORECASE): '.',
    re.compile(r'[-_\s]* NOSPAM [-_\s]*', re.IGNORECASE): '', # Remove NOSPAM
    re.compile(r'[-_\s]*REMOVE[-_\s]*', re.IGNORECASE): '', # Remove REMOVE
    # Add more based on observation
}

# (Other functions like find_emails, deobfuscate_text should be here)
def find_emails(text: str) -> set[str]:
    """Finds potential email addresses in a block of text using regex."""
    if not text:
        return set()
    try:
        # Search using the improved regex
        potential_emails = EMAIL_REGEX.findall(text)
        return set(potential_emails)
    except Exception as e:
        # Regex errors can happen with complex text
        logger.error(f"Error during regex email search: {e}")
        return set()

def deobfuscate_text(text: str) -> str:
    """Applies common de-obfuscation patterns to text."""
    if not text:
        return ""
    modified_text = text
    try:
        # Decode HTML entities first (e.g., &#64; -> @, &commat; -> @)
        modified_text = html.unescape(modified_text)

        # Apply text replacements (e.g., [at] -> @)
        for pattern, replacement in OBFUSCATION_PATTERNS.items():
            modified_text = pattern.sub(replacement, modified_text)

        # Decode URL encoding (%40 -> @) - careful not to decode too much
        modified_text = unquote(modified_text)

    except Exception as e:
        logger.warning(f"Error during text de-obfuscation: {e}")
    return modified_text


# --- Replace the existing function below in your file ---
def extract_and_clean_emails(text: str) -> set[str]:
    """
    Combines de-obfuscation and regex finding to extract cleaned emails.
    """
    cleaned_emails = set()
    if not text:
        return cleaned_emails

    # Make sure this try block is correctly indented
    try:
        # 1. De-obfuscate common patterns first
        deobfuscated_text = deobfuscate_text(text)

        # 2. Find emails in the modified text
        found = find_emails(deobfuscated_text)

        # 3. Basic cleaning/validation
        for email in found:
            # Simple validation: must contain @ and .
            # Regex already ensures this structure, but double-check
            if '@' in email and '.' in email:
                 # Trim leading/trailing whitespace/common punctuation
                 # CORRECTED LINE 106: Removed backslash before /
                 cleaned = email.strip('.,;:"/<>[](){}\\t\\n\\r ')
                 # Ensure it still looks like an email after stripping
                 if '@' in cleaned and '.' in cleaned and cleaned.count('@') == 1:
                     # Convert to lowercase for consistent storage
                     cleaned_emails.add(cleaned.lower())
                 else:
                     logger.debug(f"Skipping email after cleaning: '{email}' -> '{cleaned}'")

    # Ensure this except block is correctly indented relative to the try
    except Exception as e:
        logger.error(f"Error extracting/cleaning emails: {e}")

    # Ensure this return statement is correctly indented with the function definition
    return cleaned_emails
# --- End of function to replace ---

