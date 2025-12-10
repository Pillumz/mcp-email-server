# Email Reply Support Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add email reply/threading support by exposing Message-ID when reading emails and accepting In-Reply-To/References headers when sending.

**Architecture:** Minimal changes to existing code - extract Message-ID header during email parsing, add two optional parameters to send_email, update response models.

**Tech Stack:** Python, pytest, aiosmtplib, aioimaplib, Pydantic models

---

## Task 0: Repository Setup

**Purpose:** Set up fork with proper remotes for PR workflow.

**Step 1: Configure remotes**

```fish
cd /opt/mcp/upstream/mcp-email-server
git remote rename origin upstream
git remote add origin git@github.com:Pillumz/mcp-email-server.git
git fetch upstream
```

**Step 2: Create feature branch**

```fish
git checkout -b feature/reply-support
```

**Step 3: Verify setup**

```fish
git remote -v
# Expected:
# origin    git@github.com:Pillumz/mcp-email-server.git (fetch)
# origin    git@github.com:Pillumz/mcp-email-server.git (push)
# upstream  https://github.com/ai-zerolab/mcp-email-server.git (fetch)
# upstream  https://github.com/ai-zerolab/mcp-email-server.git (push)

git branch
# Expected: * feature/reply-support
```

---

## Task 1: Add message_id to Models

**Files:**
- Modify: `mcp_email_server/emails/models.py`
- Test: `tests/test_models.py`

**Step 1: Write the failing test**

Add to `tests/test_models.py`:

```python
def test_email_metadata_includes_message_id():
    """Test that EmailMetadata includes message_id field."""
    metadata = EmailMetadata(
        email_id="123",
        message_id="<abc123@example.com>",
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        attachments=[],
    )
    assert metadata.message_id == "<abc123@example.com>"


def test_email_metadata_message_id_optional():
    """Test that message_id can be None."""
    metadata = EmailMetadata(
        email_id="123",
        message_id=None,
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        attachments=[],
    )
    assert metadata.message_id is None


def test_email_body_response_includes_message_id():
    """Test that EmailBodyResponse includes message_id field."""
    response = EmailBodyResponse(
        email_id="123",
        message_id="<abc123@example.com>",
        subject="Test",
        sender="sender@example.com",
        recipients=["recipient@example.com"],
        date=datetime.now(timezone.utc),
        body="Test body",
        attachments=[],
    )
    assert response.message_id == "<abc123@example.com>"
```

**Step 2: Run test to verify it fails**

```fish
cd /opt/mcp/upstream/mcp-email-server
uv run pytest tests/test_models.py::test_email_metadata_includes_message_id -v
```

Expected: FAIL - `message_id` field doesn't exist

**Step 3: Implement the model changes**

Edit `mcp_email_server/emails/models.py`:

```python
class EmailMetadata(BaseModel):
    """Email metadata"""

    email_id: str
    message_id: str | None  # RFC 5322 Message-ID header for reply threading
    subject: str
    sender: str
    recipients: list[str]
    date: datetime
    attachments: list[str]

    @classmethod
    def from_email(cls, email: dict[str, Any]):
        return cls(
            email_id=email["email_id"],
            message_id=email.get("message_id"),  # NEW
            subject=email["subject"],
            sender=email["from"],
            recipients=email.get("to", []),
            date=email["date"],
            attachments=email["attachments"],
        )


class EmailBodyResponse(BaseModel):
    """Single email body response"""

    email_id: str
    message_id: str | None  # RFC 5322 Message-ID header for reply threading
    subject: str
    sender: str
    recipients: list[str]
    date: datetime
    body: str
    attachments: list[str]
```

**Step 4: Run tests to verify they pass**

```fish
uv run pytest tests/test_models.py -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/emails/models.py tests/test_models.py
git commit -m "feat(models): add message_id field for reply threading"
```

---

## Task 2: Extract Message-ID During Email Parsing

**Files:**
- Modify: `mcp_email_server/emails/classic.py` (lines 39-115, `_parse_email_data` method)
- Test: `tests/test_email_client.py`

**Step 1: Write the failing test**

Add to `tests/test_email_client.py`:

```python
class TestParseEmailData:
    def test_parse_email_extracts_message_id(self, email_client):
        """Test that Message-ID header is extracted during parsing."""
        raw_email = b"""Message-ID: <test123@example.com>
From: sender@example.com
To: recipient@example.com
Subject: Test Subject
Date: Mon, 1 Jan 2024 12:00:00 +0000

Test body content
"""
        result = email_client._parse_email_data(raw_email, email_id="1")
        assert result["message_id"] == "<test123@example.com>"

    def test_parse_email_handles_missing_message_id(self, email_client):
        """Test graceful handling when Message-ID is missing."""
        raw_email = b"""From: sender@example.com
To: recipient@example.com
Subject: Test Subject
Date: Mon, 1 Jan 2024 12:00:00 +0000

Test body content
"""
        result = email_client._parse_email_data(raw_email, email_id="1")
        assert result["message_id"] is None
```

**Step 2: Run test to verify it fails**

```fish
uv run pytest tests/test_email_client.py::TestParseEmailData::test_parse_email_extracts_message_id -v
```

Expected: FAIL - KeyError or missing `message_id`

**Step 3: Implement Message-ID extraction**

Edit `mcp_email_server/emails/classic.py`, in the `_parse_email_data` method. After line 47 (where other headers are extracted), add:

```python
# Extract Message-ID for reply threading
message_id = email_message.get("Message-ID")
```

And in the return dict (around line 107-115), add `message_id`:

```python
return {
    "email_id": email_id or "",
    "message_id": message_id,  # NEW: for reply threading
    "subject": subject,
    "from": sender,
    "to": to_addresses,
    "body": body,
    "date": date,
    "attachments": attachments,
}
```

**Step 4: Run tests to verify they pass**

```fish
uv run pytest tests/test_email_client.py::TestParseEmailData -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/emails/classic.py tests/test_email_client.py
git commit -m "feat(parsing): extract Message-ID header for reply threading"
```

---

## Task 3: Update send_email to Accept Reply Headers

**Files:**
- Modify: `mcp_email_server/emails/classic.py` (lines 522-575, `send_email` method)
- Test: `tests/test_email_client.py`

**Step 1: Write the failing tests**

Add to `tests/test_email_client.py`:

```python
class TestSendEmailReplyHeaders:
    @pytest.mark.asyncio
    async def test_send_email_sets_in_reply_to_header(self, email_client):
        """Test that In-Reply-To header is set when provided."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__.return_value = mock_smtp
        mock_smtp.__aexit__.return_value = None
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()

        with patch("aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Re: Test",
                body="Reply body",
                in_reply_to="<original123@example.com>",
            )

            call_args = mock_smtp.send_message.call_args
            msg = call_args[0][0]
            assert msg["In-Reply-To"] == "<original123@example.com>"

    @pytest.mark.asyncio
    async def test_send_email_sets_references_header(self, email_client):
        """Test that References header is set when provided."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__.return_value = mock_smtp
        mock_smtp.__aexit__.return_value = None
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()

        with patch("aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Re: Test",
                body="Reply body",
                references="<first@example.com> <second@example.com>",
            )

            call_args = mock_smtp.send_message.call_args
            msg = call_args[0][0]
            assert msg["References"] == "<first@example.com> <second@example.com>"

    @pytest.mark.asyncio
    async def test_send_email_without_reply_headers(self, email_client):
        """Test that send works without reply headers (backward compatibility)."""
        mock_smtp = AsyncMock()
        mock_smtp.__aenter__.return_value = mock_smtp
        mock_smtp.__aexit__.return_value = None
        mock_smtp.login = AsyncMock()
        mock_smtp.send_message = AsyncMock()

        with patch("aiosmtplib.SMTP", return_value=mock_smtp):
            await email_client.send_email(
                recipients=["recipient@example.com"],
                subject="Test",
                body="Body",
            )

            call_args = mock_smtp.send_message.call_args
            msg = call_args[0][0]
            assert "In-Reply-To" not in msg
            assert "References" not in msg
```

**Step 2: Run test to verify it fails**

```fish
uv run pytest tests/test_email_client.py::TestSendEmailReplyHeaders::test_send_email_sets_in_reply_to_header -v
```

Expected: FAIL - `in_reply_to` parameter not accepted

**Step 3: Implement reply headers in send_email**

Edit `mcp_email_server/emails/classic.py`. Update the `send_email` method signature (around line 522):

```python
async def send_email(
    self,
    recipients: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    attachments: list[str] | None = None,
    in_reply_to: str | None = None,  # NEW: Message-ID of email being replied to
    references: str | None = None,   # NEW: Space-separated Message-IDs for thread chain
):
```

After setting the `Cc` header (around line 555), add:

```python
# Set threading headers for replies
if in_reply_to:
    msg["In-Reply-To"] = in_reply_to
if references:
    msg["References"] = references
```

**Step 4: Run tests to verify they pass**

```fish
uv run pytest tests/test_email_client.py::TestSendEmailReplyHeaders -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/emails/classic.py tests/test_email_client.py
git commit -m "feat(send): add in_reply_to and references parameters for threading"
```

---

## Task 4: Update ClassicEmailHandler to Pass Reply Headers

**Files:**
- Modify: `mcp_email_server/emails/classic.py` (lines 679-689, `ClassicEmailHandler.send_email`)
- Test: `tests/test_classic_handler.py`

**Step 1: Write the failing test**

Add to `tests/test_classic_handler.py`:

```python
@pytest.mark.asyncio
async def test_send_email_with_reply_headers(self, classic_handler):
    """Test sending email with reply headers."""
    mock_smtp = AsyncMock()
    mock_smtp.__aenter__.return_value = mock_smtp
    mock_smtp.__aexit__.return_value = None
    mock_smtp.login = AsyncMock()
    mock_smtp.send_message = AsyncMock()

    with patch("aiosmtplib.SMTP", return_value=mock_smtp):
        await classic_handler.send_email(
            recipients=["recipient@example.com"],
            subject="Re: Test",
            body="Reply body",
            in_reply_to="<original@example.com>",
            references="<original@example.com>",
        )

        call_args = mock_smtp.send_message.call_args
        msg = call_args[0][0]
        assert msg["In-Reply-To"] == "<original@example.com>"
        assert msg["References"] == "<original@example.com>"
```

**Step 2: Run test to verify it fails**

```fish
uv run pytest tests/test_classic_handler.py::TestClassicEmailHandler::test_send_email_with_reply_headers -v
```

Expected: FAIL - parameters not passed through

**Step 3: Update ClassicEmailHandler.send_email**

Edit `mcp_email_server/emails/classic.py`. Find the `ClassicEmailHandler.send_email` method (around line 679) and update it:

```python
async def send_email(
    self,
    recipients: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: bool = False,
    attachments: list[str] | None = None,
    in_reply_to: str | None = None,  # NEW
    references: str | None = None,   # NEW
):
    await self.outgoing_client.send_email(
        recipients, subject, body, cc, bcc, html, attachments,
        in_reply_to, references,  # NEW: pass through
    )
```

**Step 4: Run tests to verify they pass**

```fish
uv run pytest tests/test_classic_handler.py -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/emails/classic.py tests/test_classic_handler.py
git commit -m "feat(handler): pass reply headers through ClassicEmailHandler"
```

---

## Task 5: Update MCP Tool send_email

**Files:**
- Modify: `mcp_email_server/app.py` (lines 105-137)
- Test: `tests/test_mcp_tools.py`

**Step 1: Write the failing test**

Add to `tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_send_email_with_reply_headers(self):
    """Test send_email MCP tool with reply headers."""
    mock_handler = AsyncMock()
    mock_handler.send_email = AsyncMock()

    with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
        result = await send_email(
            account_name="test",
            recipients=["recipient@example.com"],
            subject="Re: Test",
            body="Reply body",
            in_reply_to="<original@example.com>",
            references="<original@example.com>",
        )

        mock_handler.send_email.assert_called_once()
        call_kwargs = mock_handler.send_email.call_args
        # Check that in_reply_to and references were passed
        assert call_kwargs[1].get("in_reply_to") == "<original@example.com>" or \
               call_kwargs[0][7] == "<original@example.com>"  # positional fallback
```

**Step 2: Run test to verify it fails**

```fish
uv run pytest tests/test_mcp_tools.py::TestMcpTools::test_send_email_with_reply_headers -v
```

Expected: FAIL - parameters not accepted

**Step 3: Update app.py send_email tool**

Edit `mcp_email_server/app.py`. Update the `send_email` function signature to add the new parameters after `attachments`:

```python
@mcp.tool(
    description="Send an email using the specified account. Supports replying to emails with proper threading when in_reply_to is provided.",
)
async def send_email(
    account_name: Annotated[str, Field(description="The name of the email account to send from.")],
    recipients: Annotated[list[str], Field(description="A list of recipient email addresses.")],
    subject: Annotated[str, Field(description="The subject of the email.")],
    body: Annotated[str, Field(description="The body of the email.")],
    cc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of CC email addresses."),
    ] = None,
    bcc: Annotated[
        list[str] | None,
        Field(default=None, description="A list of BCC email addresses."),
    ] = None,
    html: Annotated[
        bool,
        Field(default=False, description="Whether to send the email as HTML (True) or plain text (False)."),
    ] = False,
    attachments: Annotated[
        list[str] | None,
        Field(
            default=None,
            description="A list of absolute file paths to attach to the email.",
        ),
    ] = None,
    in_reply_to: Annotated[
        str | None,
        Field(
            default=None,
            description="Message-ID of the email being replied to. Enables proper threading in email clients.",
        ),
    ] = None,
    references: Annotated[
        str | None,
        Field(
            default=None,
            description="Space-separated Message-IDs for the thread chain. Usually includes in_reply_to plus ancestors.",
        ),
    ] = None,
) -> str:
    handler = dispatch_handler(account_name)
    await handler.send_email(
        recipients, subject, body, cc, bcc, html, attachments,
        in_reply_to, references,  # NEW
    )
    recipient_str = ", ".join(recipients)
    attachment_info = f" with {len(attachments)} attachment(s)" if attachments else ""
    return f"Email sent successfully to {recipient_str}{attachment_info}"
```

**Step 4: Run tests to verify they pass**

```fish
uv run pytest tests/test_mcp_tools.py -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/app.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add in_reply_to and references params to send_email tool"
```

---

## Task 6: Update get_emails_content to Return message_id

**Files:**
- Modify: `mcp_email_server/emails/classic.py` (where EmailBodyResponse is constructed)
- Test: `tests/test_mcp_tools.py`

**Step 1: Write the failing test**

Add to `tests/test_mcp_tools.py`:

```python
@pytest.mark.asyncio
async def test_get_emails_content_includes_message_id(self):
    """Test that get_emails_content returns message_id."""
    mock_handler = AsyncMock()
    mock_handler.get_emails_content = AsyncMock(
        return_value=EmailContentBatchResponse(
            emails=[
                EmailBodyResponse(
                    email_id="123",
                    message_id="<test@example.com>",
                    subject="Test",
                    sender="sender@example.com",
                    recipients=["recipient@example.com"],
                    date=datetime.now(timezone.utc),
                    body="Test body",
                    attachments=[],
                )
            ],
            requested_count=1,
            retrieved_count=1,
            failed_ids=[],
        )
    )

    with patch("mcp_email_server.app.dispatch_handler", return_value=mock_handler):
        result = await get_emails_content(
            account_name="test",
            email_ids=["123"],
        )

        assert result.emails[0].message_id == "<test@example.com>"
```

**Step 2: Run test to verify behavior**

```fish
uv run pytest tests/test_mcp_tools.py::TestMcpTools::test_get_emails_content_includes_message_id -v
```

This should pass if models are updated correctly. If not, trace where EmailBodyResponse is constructed.

**Step 3: Update EmailBodyResponse construction in classic.py**

Find where `EmailBodyResponse` is constructed (search for `EmailBodyResponse(`) and ensure `message_id` is passed:

```python
EmailBodyResponse(
    email_id=email_data["email_id"],
    message_id=email_data.get("message_id"),  # NEW
    subject=email_data["subject"],
    sender=email_data["from"],
    recipients=email_data.get("to", []),
    date=email_data["date"],
    body=email_data.get("body", ""),
    attachments=email_data.get("attachments", []),
)
```

**Step 4: Run full test suite**

```fish
uv run pytest tests/ -v
```

Expected: All PASS

**Step 5: Commit**

```fish
git add mcp_email_server/emails/classic.py tests/test_mcp_tools.py
git commit -m "feat(content): include message_id in EmailBodyResponse"
```

---

## Task 7: Update README Documentation

**Files:**
- Modify: `README.md`

**Step 1: Add reply documentation**

Add a new section to README.md after the main usage examples:

```markdown
### Replying to Emails

To reply to an email with proper threading (so it appears in the same conversation in email clients):

1. First, fetch the original email to get its `message_id`:

```python
emails = await get_emails_content(account_name="work", email_ids=["123"])
original = emails.emails[0]
```

2. Send your reply using `in_reply_to` and `references`:

```python
await send_email(
    account_name="work",
    recipients=[original.sender],
    subject=f"Re: {original.subject}",
    body="Thank you for your email...",
    in_reply_to=original.message_id,
    references=original.message_id,
)
```

The `in_reply_to` parameter sets the `In-Reply-To` header, and `references` sets the `References` header. Both are used by email clients to thread conversations properly.
```

**Step 2: Commit**

```fish
git add README.md
git commit -m "docs: add reply/threading usage example"
```

---

## Task 8: Final Verification and PR Preparation

**Step 1: Run all checks**

```fish
cd /opt/mcp/upstream/mcp-email-server
make check
make test
```

Expected: All pass

**Step 2: Review all changes**

```fish
git log --oneline feature/reply-support ^main
git diff main...feature/reply-support --stat
```

**Step 3: Push to fork**

```fish
git push -u origin feature/reply-support
```

**Step 4: Create PR**

```fish
gh pr create --repo ai-zerolab/mcp-email-server \
  --title "feat: Add reply support with In-Reply-To and References headers" \
  --body "$(cat <<'EOF'
## Summary

Adds email reply/threading support:
- Exposes `message_id` (RFC 5322 Message-ID) when reading emails
- Adds `in_reply_to` and `references` parameters to `send_email`

This enables proper email threading in clients when replying to emails.

## Changes

- **models.py**: Added `message_id: str | None` to `EmailMetadata` and `EmailBodyResponse`
- **classic.py**: Extract Message-ID during parsing, set In-Reply-To/References when sending
- **app.py**: Added `in_reply_to` and `references` parameters to send_email tool
- **README.md**: Added usage documentation for reply feature
- **tests/**: Added comprehensive tests for all new functionality

## Backward Compatibility

All changes are additive - existing API calls work unchanged.

## Testing

All existing tests pass. New tests added for:
- Message-ID extraction (with and without header)
- In-Reply-To header setting
- References header setting
- Backward compatibility (send without reply headers)
EOF
)"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 0 | Repository setup | git config |
| 1 | Add message_id to models | models.py, test_models.py |
| 2 | Extract Message-ID during parsing | classic.py, test_email_client.py |
| 3 | Add reply params to EmailClient.send_email | classic.py, test_email_client.py |
| 4 | Pass reply params through ClassicEmailHandler | classic.py, test_classic_handler.py |
| 5 | Add reply params to MCP send_email tool | app.py, test_mcp_tools.py |
| 6 | Return message_id in get_emails_content | classic.py, test_mcp_tools.py |
| 7 | Update README | README.md |
| 8 | Final verification and PR | - |
