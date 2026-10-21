"""Web UI for ubuntu-server-auto-setup: configure the inventory and run the playbooks from a browser."""

import os

# Release images set USAS_VERSION from the tag (the Dockerfile's USAS_VERSION build argument, passed by
# .github/workflows/release.yml); a checkout or a local image build says "dev".
__version__ = os.environ.get("USAS_VERSION", "").strip() or "dev"
