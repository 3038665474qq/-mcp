"""Open the installed Codex CLI's normal interactive login flow."""
import subprocess
import sys
from publish_html import find_codex

if __name__ == '__main__':
    command = find_codex()
    if isinstance(command, str):
        command = [command]
    sys.exit(subprocess.call([*command, 'login'], shell=False))
