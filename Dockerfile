# The web UI image: ansible-core, the collections, the playbooks and roles, and the web UI, running as a
# non-root user with everything it writes in /data. Build it with `docker build -t usas .`; run it as in the
# README: docker run -d -p 127.0.0.1:8080:8080 -v usas-data:/data honeybeartech/ubuntu-server-auto-setup
# Base images are pinned by digest; Dependabot keeps them current (.github/dependabot.yml).

# The web UI's app (webui/frontend), built into static files. Only those files reach the runtime image: no
# Node, npm or node_modules. npm ci installs exactly the lock file's versions and checks each package's
# integrity hash; no dependency runs an install script (.npmrc). The files don't depend on the platform, so this
# stage runs on the build machine's own platform, also for a multi-arch build.
FROM --platform=$BUILDPLATFORM node:24-slim@sha256:8cabefbd444809cf0e319b5831c65da1ea202200ca552a5e75fd7938a6f86658 AS frontend
WORKDIR /build/webui/frontend
COPY webui/frontend/package.json webui/frontend/package-lock.json webui/frontend/.npmrc ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY webui/frontend/tsconfig.json webui/frontend/vite.config.ts ./
COPY webui/frontend/src ./src
RUN npm run build \
 && mkdir -p /build/licenses \
 && cp node_modules/react/LICENSE /build/licenses/react.txt \
 && cp node_modules/react-dom/LICENSE /build/licenses/react-dom.txt \
 && cp node_modules/scheduler/LICENSE /build/licenses/scheduler.txt \
 && cp node_modules/@fontsource/atkinson-hyperlegible-next/LICENSE /build/licenses/atkinson-hyperlegible-next.txt \
 && cp node_modules/@fontsource/atkinson-hyperlegible-mono/LICENSE /build/licenses/atkinson-hyperlegible-mono.txt

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt requirements-webui.in requirements-webui.txt requirements.yml ./
COPY scripts/retry.sh scripts/
# Exact versions with hashes (requirements-webui.txt), wheels only: what the image runs was reviewed.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements-webui.txt
# The collections ansible.cfg looks for in collections/ (collections_path) next to it. Galaxy fails now and
# then, so the install is tried up to four times (scripts/retry.sh).
RUN scripts/retry.sh /opt/venv/bin/ansible-galaxy collection install -r requirements.yml -p /opt/collections \
 && find /opt/collections -type d -name tests -prune -exec rm -rf {} + \
 && /opt/venv/bin/ansible-galaxy collection list -p /opt/collections > /opt/collections/VERSIONS.txt
# Nothing installs packages at run time, so the copied environment carries no pip (and none of the libraries
# pip vendors, which scanners rightly report when they fall behind).
RUN /opt/venv/bin/python -m pip uninstall --yes pip

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime
# Security fixes published since the pinned base image. A release passes its run ID, so a build cache
# can't reuse an older upgrade layer.
ARG SECURITY_UPDATES=
# openssh-client: Ansible's SSH connection, ssh-keyscan and ssh-keygen. tzdata: the list of time zones.
# Debian package versions aren't pinned: the security archive replaces them, so a pin would break the build
# exactly when a fix ships. The base image is pinned by digest instead.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends openssh-client tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall --yes pip \
 && rm -rf /usr/local/lib/python3.*/ensurepip/_bundled \
 && useradd --uid 10001 --user-group --create-home --home-dir /home/usas --shell /usr/sbin/nologin usas \
 && install -d -m 0700 -o usas -g usas /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/collections /app/collections
COPY ansible.cfg LICENSE requirements.txt requirements.yml /app/
COPY playbooks /app/playbooks
COPY roles /app/roles
COPY plugins /app/plugins
COPY callback_plugins /app/callback_plugins
COPY inventory/hosts.example.yml inventory/vault.example.yml /app/inventory/
COPY webui/usas_web /app/webui/usas_web
COPY --from=frontend /build/webui/usas_web/static/app /app/webui/usas_web/static/app
COPY LICENSE /usr/share/licenses/ubuntu-server-auto-setup/LICENSE
# The licences of what the app's files contain: React (MIT) and the Atkinson Hyperlegible fonts (OFL 1.1).
COPY --from=frontend /build/licenses /usr/share/licenses/ubuntu-server-auto-setup/third-party
# The version the web UI shows: the release workflow passes the tag without its v; local builds say "dev".
ARG USAS_VERSION=dev
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/app/webui \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/usas \
    ANSIBLE_CONFIG=/app/ansible.cfg \
    USAS_PROJECT_DIR=/app \
    USAS_DATA_DIR=/data \
    USAS_VERSION=${USAS_VERSION}
LABEL org.opencontainers.image.title="ubuntu-server-auto-setup" \
      org.opencontainers.image.description="Install Ubuntu Server on Proxmox VE VMs hands-off and harden them, from a web UI" \
      org.opencontainers.image.source="https://github.com/HoneyBearTech/ubuntu-server-auto-setup" \
      org.opencontainers.image.url="https://github.com/HoneyBearTech/ubuntu-server-auto-setup" \
      org.opencontainers.image.documentation="https://github.com/HoneyBearTech/ubuntu-server-auto-setup/blob/main/docs/web-ui.md" \
      org.opencontainers.image.licenses="MIT"
WORKDIR /app
USER 10001:10001
VOLUME ["/data"]
# Publish it on 127.0.0.1 only (-p 127.0.0.1:8080:8080); see "Exposing it" in docs/web-ui.md.
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)"]
CMD ["python", "-m", "usas_web"]
