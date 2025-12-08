from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class EmailMetadata(BaseModel):
    """Email metadata"""

    email_id: str
    subject: str
    sender: str
    recipients: list[str]  # Recipient list
    date: datetime
    attachments: list[str]

    @classmethod
    def from_email(cls, email: dict[str, Any]):
        return cls(
            email_id=email["email_id"],
            subject=email["subject"],
            sender=email["from"],
            recipients=email.get("to", []),
            date=email["date"],
            attachments=email["attachments"],
        )


class EmailMetadataPageResponse(BaseModel):
    """Paged email metadata response"""

    page: int
    page_size: int
    before: datetime | None
    since: datetime | None
    subject: str | None
    emails: list[EmailMetadata]
    total: int


class EmailBodyResponse(BaseModel):
    """Single email body response"""

    email_id: str  # IMAP UID of this email
    status: Literal["ok", "not_found"] = "ok"  # Status of the email fetch
    status_message: str | None = None  # Error message if status is not_found
    subject: str = ""
    sender: str = ""
    recipients: list[str] = []
    date: datetime | None = None
    body: str = ""
    attachments: list[str] = []
    message_id: str = ""  # Message-ID header for threading
    in_reply_to: str = ""  # In-Reply-To header (if this is a reply)
    references: str = ""  # References header (thread chain)
    web_url: str | None = None  # Direct link to web interface (Yandex Mail)

    @classmethod
    def not_found(cls, email_id: str, message: str = "Email no longer exists on server"):
        """Create a not_found response for a missing email."""
        return cls(
            email_id=email_id,
            status="not_found",
            status_message=message,
        )


class EmailContentBatchResponse(BaseModel):
    """Batch email content response for multiple emails"""

    emails: list[EmailBodyResponse]
    requested_count: int
    retrieved_count: int
    failed_ids: list[str]


class AttachmentDownloadResponse(BaseModel):
    """Attachment download response"""

    email_id: str
    attachment_name: str
    mime_type: str
    size: int
    saved_path: str
