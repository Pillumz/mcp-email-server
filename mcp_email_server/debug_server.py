"""Debug server to expose email message data for URL link verification.

This server exposes message UID, generated URL, subject, and body text
so that a browser agent can compare them against the actual web interface.
"""

import asyncio
import html
from dataclasses import dataclass
from datetime import datetime

import gradio as gr

from mcp_email_server import cache
from mcp_email_server.config import get_settings
from mcp_email_server.emails.classic import ClassicEmailHandler
from mcp_email_server.emails.yandex_links import YandexLinkCalculator, decode_imap_utf7
from mcp_email_server.log import logger


@dataclass
class MessageDebugInfo:
    """Debug info for a single message."""
    uid: int
    folder: str
    web_url: str | None
    subject: str
    sender: str
    date: datetime  # Date header (sent time)
    internal_date: datetime | None  # IMAP internal date (arrival time)
    body_preview: str


def get_cached_info(email_settings, folder: str, uid: int) -> tuple[str | None, datetime | None]:
    """Get web URL and internal_date from cache only (no sync).

    Returns:
        Tuple of (web_url, internal_date)
    """
    if not email_settings.yandex_link or not email_settings.yandex_link.enabled:
        return None, None

    cache.init_db()
    account = email_settings.account_name

    # Get full cached message info
    conn = cache._get_connection()
    try:
        cursor = conn.execute(
            "SELECT mid, tid, internal_date FROM message_index WHERE account = ? AND folder = ? AND uid = ?",
            (account, folder, uid)
        )
        row = cursor.fetchone()
        if row:
            mid = row["mid"]
            tid = row["tid"] if row["tid"] else mid  # Fallback to mid if tid is NULL
            internal_date = datetime.fromisoformat(row["internal_date"]) if row["internal_date"] else None

            # Format URL using TID (thread ID)
            decoded_folder = decode_imap_utf7(folder)
            folder_id = email_settings.yandex_link.folder_ids.get(decoded_folder, 1)
            web_url = f"https://{email_settings.yandex_link.url_prefix}/touch/folder/{folder_id}/thread/{tid}"

            return web_url, internal_date
    finally:
        conn.close()

    return None, None


async def fetch_messages_async(account_name: str, count: int, use_mcp_logic: bool = True) -> list[MessageDebugInfo]:
    """Fetch messages from all folders for the specified account.

    Args:
        account_name: Name of the email account
        count: Number of messages to fetch
        use_mcp_logic: If True, use same logic as MCP (with sync). If False, cache-only (fast).
    """
    settings = get_settings()

    # Find the account
    email_settings = None
    for acc in settings.emails:
        if acc.account_name == account_name:
            email_settings = acc
            break

    if not email_settings:
        raise ValueError(f"Account '{account_name}' not found")

    handler = ClassicEmailHandler(email_settings)

    # Get all folders
    folders = await handler.list_folders()
    logger.info(f"Found folders: {folders}")

    all_messages = []

    # Fetch from each folder
    for folder in folders:
        try:
            # Get email metadata (list)
            metadata = await handler.get_emails_metadata(
                page=1,
                page_size=count,
                mailbox=folder,
                order="desc"
            )

            if not metadata.emails:
                continue

            # Get email IDs
            email_ids = [e.email_id for e in metadata.emails]

            if use_mcp_logic:
                # Use exact same logic as MCP - calls YandexLinkCalculator with sync
                content = await handler.get_emails_content(email_ids, mailbox=folder)

                for email in content.emails:
                    # Get internal_date from cache for sorting
                    _, internal_date = get_cached_info(email_settings, folder, int(email.email_id))

                    all_messages.append(MessageDebugInfo(
                        uid=int(email.email_id),
                        folder=folder,
                        web_url=email.web_url,
                        subject=email.subject,
                        sender=email.sender,
                        date=email.date,
                        internal_date=internal_date,
                        body_preview=email.body[:500] if email.body else "",
                    ))
            else:
                # Fast mode - cache only, no sync
                for email_id in email_ids:
                    try:
                        email_data = await handler.incoming_client.get_email_body_by_id(email_id, folder)
                        if email_data:
                            web_url, internal_date = get_cached_info(email_settings, folder, int(email_id))

                            all_messages.append(MessageDebugInfo(
                                uid=int(email_id),
                                folder=folder,
                                web_url=web_url,
                                subject=email_data.get("subject", ""),
                                sender=email_data.get("from", ""),
                                date=email_data.get("date", datetime.now()),
                                internal_date=internal_date,
                                body_preview=email_data.get("body", "")[:500] if email_data.get("body") else "",
                            ))
                    except Exception as e:
                        logger.warning(f"Error fetching email {email_id} from {folder}: {e}")

        except Exception as e:
            logger.warning(f"Error fetching from folder {folder}: {e}")
            continue

    # Sort by internal_date (arrival time) descending, fallback to date header
    all_messages.sort(key=lambda m: m.internal_date or m.date, reverse=True)

    # Limit total
    return all_messages[:count]


def fetch_messages(account_name: str, count: int, use_mcp_logic: bool = True) -> str:
    """Sync wrapper for fetch_messages_async - returns plain text."""
    try:
        messages = asyncio.run(fetch_messages_async(account_name, int(count), use_mcp_logic))

        if not messages:
            return "No messages found."

        result = []
        for msg in messages:
            result.append(f"""
================================================================================
UID: {msg.uid}
FOLDER: {msg.folder}
WEB_URL: {msg.web_url or 'NOT CACHED'}
SUBJECT: {msg.subject}
FROM: {msg.sender}
ARRIVED: {msg.internal_date or 'N/A'}
SENT: {msg.date}
--------------------------------------------------------------------------------
BODY PREVIEW:
{msg.body_preview}
================================================================================
""")
        return "\n".join(result)
    except Exception as e:
        logger.exception("Error fetching messages")
        return f"Error: {e}"


def fetch_messages_html(account_name: str, count: int, use_mcp_logic: bool = True) -> str:
    """Fetch messages and return as HTML table with clickable links."""
    try:
        messages = asyncio.run(fetch_messages_async(account_name, int(count), use_mcp_logic))

        if not messages:
            return "<p>No messages found.</p>"

        rows = []
        for msg in messages:
            if msg.web_url:
                url_cell = f'<a href="{html.escape(msg.web_url)}" target="_blank">{html.escape(msg.web_url)}</a>'
            else:
                url_cell = '<span style="color:#999">NOT CACHED</span>'

            arrived = html.escape(str(msg.internal_date)) if msg.internal_date else "N/A"
            rows.append(f"""
            <tr>
                <td><strong>{msg.uid}</strong></td>
                <td>{html.escape(msg.folder)}</td>
                <td>{url_cell}</td>
                <td>{html.escape(msg.subject)}</td>
                <td>{html.escape(msg.sender)}</td>
                <td>{arrived}</td>
            </tr>
            <tr>
                <td colspan="6" style="background:#f5f5f5; padding:10px;">
                    <details>
                        <summary>Body Preview (click to expand)</summary>
                        <pre style="white-space:pre-wrap; word-wrap:break-word;">{html.escape(msg.body_preview)}</pre>
                    </details>
                </td>
            </tr>
            """)

        table_html = f"""
        <style>
            .debug-table {{ border-collapse: collapse; width: 100%; font-family: monospace; }}
            .debug-table th, .debug-table td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            .debug-table th {{ background-color: #4a90d9; color: white; }}
            .debug-table tr:nth-child(4n+1) {{ background-color: #f9f9f9; }}
            .debug-table a {{ color: #0066cc; }}
        </style>
        <table class="debug-table">
            <thead>
                <tr>
                    <th>UID</th>
                    <th>Folder</th>
                    <th>Generated Web URL</th>
                    <th>Subject</th>
                    <th>From</th>
                    <th>Arrived</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
        """
        return table_html
    except Exception as e:
        logger.exception("Error fetching messages")
        return f"<p style='color:red'>Error: {html.escape(str(e))}</p>"


def get_available_accounts() -> list[str]:
    """Get list of available account names."""
    try:
        settings = get_settings()
        return [acc.account_name for acc in settings.emails]
    except Exception as e:
        logger.error(f"Error getting accounts: {e}")
        return []


def get_cache_info() -> str:
    """Get cache statistics."""
    try:
        cache.init_db()
        stats = cache.get_cache_stats()
        return f"Cache: {stats['total_messages']} messages cached\nPath: {stats['cache_path']}\nPer account: {stats['per_account']}"
    except Exception as e:
        return f"Error: {e}"


async def sync_from_web_api_async(account_name: str) -> str:
    """Sync MIDs from Yandex Web API for the specified account."""
    settings = get_settings()

    # Find the account
    email_settings = None
    for acc in settings.emails:
        if acc.account_name == account_name:
            email_settings = acc
            break

    if not email_settings:
        return f"Error: Account '{account_name}' not found"

    if not email_settings.yandex_link or not email_settings.yandex_link.enabled:
        return "Error: Yandex link not enabled for this account"

    if not email_settings.yandex_link.cookies_file:
        return "Error: No cookies file configured. Set yandex_link.cookies_file in config."

    handler = ClassicEmailHandler(email_settings)
    calculator = YandexLinkCalculator(email_settings)

    # Get all folders
    folders = await handler.list_folders()
    logger.info(f"Syncing from folders: {folders}")

    # Gather IMAP messages by folder
    imap_messages_by_folder: dict[str, list[dict]] = {}

    for folder in folders:
        try:
            metadata = await handler.get_emails_metadata(
                page=1,
                page_size=50,
                mailbox=folder,
                order="desc"
            )

            if not metadata.emails:
                continue

            # Get full email data for matching
            email_ids = [e.email_id for e in metadata.emails]
            messages = []

            for email_id in email_ids:
                try:
                    email_data = await handler.incoming_client.get_email_body_by_id(email_id, folder)
                    if email_data:
                        messages.append({
                            "uid": int(email_id),
                            "subject": email_data.get("subject", ""),
                            "date": email_data.get("date"),
                        })
                except Exception as e:
                    logger.warning(f"Error fetching email {email_id}: {e}")

            if messages:
                imap_messages_by_folder[folder] = messages

        except Exception as e:
            logger.warning(f"Error fetching from folder {folder}: {e}")

    if not imap_messages_by_folder:
        return "No IMAP messages found to sync"

    # Sync from Web API
    try:
        synced = await calculator.sync_from_web_api(imap_messages_by_folder, count_per_folder=50)
        return f"Successfully synced {synced} MIDs from Web API"
    except Exception as e:
        logger.exception("Error syncing from Web API")
        return f"Error syncing: {e}"


def sync_from_web_api(account_name: str) -> str:
    """Sync wrapper for sync_from_web_api_async."""
    try:
        return asyncio.run(sync_from_web_api_async(account_name))
    except Exception as e:
        logger.exception("Error in sync")
        return f"Error: {e}"


def create_ui() -> gr.Blocks:
    """Create the Gradio UI."""
    accounts = get_available_accounts()

    with gr.Blocks(title="Email Debug Server") as demo:
        gr.Markdown("# Email Debug Server")
        gr.Markdown("Expose message data for URL link verification.")

        with gr.Row():
            account_dropdown = gr.Dropdown(
                choices=accounts,
                value=accounts[0] if accounts else None,
                label="Account",
            )
            count_input = gr.Number(
                value=10,
                label="Number of messages (from all folders)",
                minimum=1,
                maximum=50,
            )
            use_mcp_checkbox = gr.Checkbox(
                value=True,
                label="Use MCP logic (sync & cache - slower but accurate)",
            )

        with gr.Row():
            fetch_btn = gr.Button("Fetch Messages", variant="primary")
            sync_btn = gr.Button("Sync from Web API", variant="secondary")

        sync_status = gr.Textbox(
            label="Sync Status",
            lines=1,
        )

        cache_info = gr.Textbox(
            label="Cache Info",
            value=get_cache_info(),
            lines=3,
        )

        with gr.Tabs():
            with gr.TabItem("HTML Table (with clickable links)"):
                html_output = gr.HTML(label="Messages Table")

            with gr.TabItem("Plain Text"):
                text_output = gr.Textbox(
                    label="Messages",
                    lines=30,
                    max_lines=100,
                )

        fetch_btn.click(
            fn=fetch_messages_html,
            inputs=[account_dropdown, count_input, use_mcp_checkbox],
            outputs=html_output,
        )
        fetch_btn.click(
            fn=fetch_messages,
            inputs=[account_dropdown, count_input, use_mcp_checkbox],
            outputs=text_output,
        )

        def sync_and_update_cache(account_name: str) -> tuple[str, str]:
            """Sync from Web API and return updated cache info."""
            result = sync_from_web_api(account_name)
            return result, get_cache_info()

        sync_btn.click(
            fn=sync_and_update_cache,
            inputs=[account_dropdown],
            outputs=[sync_status, cache_info],
        )

    return demo


def run_server(host: str = "0.0.0.0", port: int = 7860, root_path: str = ""):
    """Run the debug server."""
    demo = create_ui()
    demo.launch(server_name=host, server_port=port, share=False, root_path=root_path)


if __name__ == "__main__":
    run_server()
