import json
import os
import platform
from pathlib import Path

from jinja2 import Template

_HERE = Path(__file__).parent
config_template = _HERE / "claude_desktop_config.json"


def generate_claude_config():
    # Get current working directory
    pwd = Path.cwd().resolve().as_posix()

    # Read the template config

    template_content = config_template.read_text()
    rendered_content = Template(template_content).render(PWD=pwd)
    template_config = json.loads(rendered_content)

    # Determine the correct Claude config path based on the OS
    system = platform.system()
    if system == "Darwin":
        config_path = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
    elif system == "Windows":
        config_path = os.path.join(os.environ["APPDATA"], "Claude", "claude_desktop_config.json")
    else:
        print("Unsupported operating system.")
        return

    # Read the existing config file or create an empty JSON object
    try:
        with open(config_path) as f:
            existing_config = json.load(f)
    except FileNotFoundError:
        existing_config = {}

    # Merge the template config into the existing config
    if "mcpServers" not in existing_config:
        existing_config["mcpServers"] = {}
    existing_config["mcpServers"].update(template_config["mcpServers"])

    # Write the merged config back to the Claude config file
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(existing_config, f, indent=4)

    print(
        f"""
Claude Desktop configuration generated successfully.

$cat {config_path}
{json.dumps(existing_config, indent=4)}
"""
    )


if __name__ == "__main__":
    generate_claude_config()
