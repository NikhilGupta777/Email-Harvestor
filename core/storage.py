# email_harvester/core/storage.py

import logging
import threading # Use threading Lock for synchronous methods

logger = logging.getLogger(__name__)

class EmailStorage:
    """Manages storing unique email addresses found."""

    def __init__(self):
        self._found_emails = set()
        # Use threading.Lock for synchronous methods if accessed from multiple threads
        # In this specific case (called after async finishes), might not be strictly needed
        # but good practice if the class could be used elsewhere.
        self._lock = threading.Lock()

    # Note: add_email and add_emails are called from async workers.
    # They need to remain somewhat async-friendly or thread-safe.
    # Let's keep the lock for safety, assuming workers might call these.
    # If performance becomes an issue, finer-grained locking or async-specific
    # structures could be used, but Lock is generally safe.

    def add_email(self, email: str):
        """Adds a single email address to the set (thread-safe)."""
        if not email: # Ensure not empty
            return

        email_lower = email.strip().lower()
        if "@" not in email_lower or "." not in email_lower or email_lower.count('@') != 1:
            logger.debug(f"Skipping potentially invalid email format: {email}")
            return

        with self._lock:
            if email_lower not in self._found_emails:
                logger.debug(f"Found new email: {email_lower}")
                self._found_emails.add(email_lower)

    def add_emails(self, emails: set[str]):
        """Adds a set of email addresses (thread-safe)."""
        if not emails:
            return

        valid_emails_to_add = set()
        for e in emails:
            if e:
                email_lower = e.strip().lower()
                if "@" in email_lower and "." in email_lower and email_lower.count('@') == 1:
                    valid_emails_to_add.add(email_lower)
                else:
                    logger.debug(f"Skipping potentially invalid email format during bulk add: {e}")

        if not valid_emails_to_add:
            return

        with self._lock:
            original_count = len(self._found_emails)
            newly_added_set = valid_emails_to_add - self._found_emails
            self._found_emails.update(newly_added_set)
            newly_added_count = len(newly_added_set)

            if newly_added_count > 0:
                 # Log count info (this might be called from multiple async workers concurrently)
                 # Consider if this logging needs rate limiting or aggregation later
                 logger.info(f"Added {newly_added_count} new unique email(s). Total unique: {len(self._found_emails)}")


    # --- Make get_emails, count, save_to_file synchronous ---

    def get_emails(self) -> set[str]:
        """Returns the set of unique emails found (synchronous)."""
        with self._lock:
            # Return a copy to prevent modification outside the lock
            return self._found_emails.copy()

    def count(self) -> int:
        """Returns the number of unique emails found (synchronous)."""
        with self._lock:
            return len(self._found_emails)

    def save_to_file(self, filename: str):
        """Saves the found emails to a file, one per line (synchronous)."""
        email_count = self.count() # Call the synchronous count method
        logger.info(f"Attempting to save {email_count} emails to {filename}...")
        if email_count == 0:
             logger.info("No emails to save.")
             return

        # Call the synchronous get_emails method
        emails_to_save = sorted(list(self.get_emails()))

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                for email in emails_to_save:
                    f.write(email + '\n')
            logger.info(f"Successfully saved emails to {filename}")
        except IOError as e:
            logger.error(f"Failed to save emails to {filename}: {e}")
        except Exception as e:
            logger.critical(f"An unexpected error occurred during file saving: {e}")

