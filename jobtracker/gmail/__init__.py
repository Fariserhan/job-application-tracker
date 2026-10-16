"""Gmail access: OAuth, search, quota-aware batched fetching, MIME body extraction."""
from .client import GmailClient, MailSource

__all__ = ["GmailClient", "MailSource"]
