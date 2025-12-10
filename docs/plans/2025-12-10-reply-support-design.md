# Email Reply Support Design

## Overview

Add email reply/threading support to mcp-email-server by:
1. Exposing `message_id` (RFC 5322 Message-ID) when reading emails
2. Adding `in_reply_to` and `references` parameters to `send_email`

## Repository Strategy

Fork-based workflow for clean PR with upstream sync capability:

- Fork `ai-zerolab/mcp-email-server` to personal GitHub
- Configure remotes: `origin` (fork), `upstream` (original)
- Feature branch: `feature/reply-support`
- Keep `main` synced with upstream, rebase feature branch as needed
- If PR declined: maintain fork, continue pulling upstream changes

## Changes

### 1. Models (`models.py`)

Add `message_id: str | None` to both response models:

```python
class EmailMetadata(BaseModel):
    email_id: str
    message_id: str | None  # RFC 5322 Message-ID header
    subject: str
    sender: str
    recipients: list[str]
    date: datetime
    attachments: list[str]

class EmailBodyResponse(BaseModel):
    email_id: str
    message_id: str | None  # RFC 5322 Message-ID header
    subject: str
    sender: str
    recipients: list[str]
    date: datetime
    body: str
    attachments: list[str]
```

Update `EmailMetadata.from_email()` to include `message_id=email.get("message_id")`.

### 2. Email Parsing (`classic.py`)

Extract Message-ID header during IMAP fetch:

```python
message_id = msg.get("Message-ID")  # Returns None if missing

return {
    "email_id": uid,
    "message_id": message_id,
    # ... existing fields
}
```

### 3. Send Email (`app.py` + `classic.py`)

Add two optional parameters to `send_email`:

```python
in_reply_to: Annotated[str | None, Field(
    default=None,
    description="Message-ID of the email being replied to. Enables threading."
)] = None,
references: Annotated[str | None, Field(
    default=None,
    description="Space-separated Message-IDs for thread chain."
)] = None,
```

Implementation sets headers when provided:

```python
if in_reply_to:
    msg["In-Reply-To"] = in_reply_to
if references:
    msg["References"] = references
```

### 4. Testing

Unit tests with mocked IMAP/SMTP:

- `test_parse_email_extracts_message_id` - verify extraction works
- `test_parse_email_handles_missing_message_id` - verify None when missing
- `test_send_email_sets_in_reply_to_header` - verify header set
- `test_send_email_sets_references_header` - verify header set
- `test_send_email_without_reply_headers` - verify backward compatibility

### 5. Documentation

Update README.md with reply usage example:

```python
# Get original email
emails = await get_emails_content(account_name="work", email_ids=["123"])
original = emails.emails[0]

# Send reply
await send_email(
    account_name="work",
    recipients=[original.sender],
    subject=f"Re: {original.subject}",
    body="Thanks for your email...",
    in_reply_to=original.message_id,
    references=original.message_id,
)
```

## Backward Compatibility

All changes are additive:
- New `message_id` field in responses (nullable)
- New optional parameters in `send_email`
- Existing API calls continue to work unchanged

## PR Checklist

- [ ] Fork repository and configure remotes
- [ ] Create feature branch
- [ ] Implement model changes
- [ ] Implement parsing changes
- [ ] Implement send_email changes
- [ ] Write tests
- [ ] Update README
- [ ] Run `make check` and `make test`
- [ ] Submit PR with clear description
