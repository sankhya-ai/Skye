"""Start root on Maritime: listen on Maritime's $PORT on every interface, keep everything under /data.

Maritime asks for a real program here rather than a shell string. Settings,
sessions, memory and root's sign-in token live in $TURA_CODE_HOME (/data/root);
set TURA_CODE_TOKEN as a secret to choose the token yourself.
"""

import os
import sys

port = os.environ.get("PORT", "18789")
os.makedirs(os.environ.setdefault("TURA_CODE_HOME", "/data/root"), exist_ok=True)
# Listening beyond localhost is deliberate here: the VM is reached only through Maritime's front door.
os.environ.setdefault("TURA_CODE_ALLOWED_HOSTS", "localhost")

from tura_code.cli import main  # noqa: E402

sys.exit(main(["serve", "--host", "0.0.0.0", "--port", port]))
