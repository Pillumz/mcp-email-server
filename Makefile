.PHONY: install run dev test clean service-install service-start service-stop service-restart service-logs

# Package name (override in each project)
MODULE := mcp_email_server
PORT := 9082

install:
	uv sync

run:
	uv run python -m $(MODULE) streamable-http --host 127.0.0.1 --port $(PORT)

dev:
	uv run python -m $(MODULE) streamable-http --host 127.0.0.1 --port $(PORT)

stdio:
	uv run python -m $(MODULE) stdio

test:
	uv run pytest

clean:
	rm -rf .venv __pycache__ .pytest_cache .ruff_cache

# Systemd service management
service-install:
	sudo cp email-mcp.service /etc/systemd/system/
	sudo systemctl daemon-reload
	sudo systemctl enable email-mcp

service-start:
	sudo systemctl start email-mcp

service-stop:
	sudo systemctl stop email-mcp

service-restart:
	sudo systemctl restart email-mcp

service-logs:
	journalctl -u email-mcp -f
