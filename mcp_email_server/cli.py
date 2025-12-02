import os

import typer

from mcp_email_server.app import mcp
from mcp_email_server.config import delete_settings

app = typer.Typer()


@app.command()
def stdio():
    mcp.run(transport="stdio")


@app.command()
def sse(
    host: str = "localhost",
    port: int = 9557,
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


@app.command()
def streamable_http(
    host: str = os.environ.get("MCP_HOST", "localhost"),
    port: int = os.environ.get("MCP_PORT", 9557),
):
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")


@app.command()
def ui():
    from mcp_email_server.ui import main as ui_main

    ui_main()


@app.command()
def reset():
    delete_settings()
    typer.echo("✅ Config reset")


@app.command()
def debug(
    host: str = "0.0.0.0",
    port: int = 7860,
    root_path: str = "",
):
    """Run debug server to expose message data for URL verification."""
    from mcp_email_server.debug_server import run_server

    typer.echo(f"Starting debug server at http://{host}:{port}")
    run_server(host=host, port=port, root_path=root_path)


if __name__ == "__main__":
    app(["stdio"])
