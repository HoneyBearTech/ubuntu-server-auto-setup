# The web UI image: ansible-core, the collections, the playbooks and roles, and the web UI, running as a
# non-root user with everything it writes in /data. Build it with `docker build -t usas .`; run it as in the
# README: docker run -d -p 127.0.0.1:8080:8080 -v usas-data:/data honeybeartech/ubuntu-server-auto-setup
# Base images are pinned by digest; Dependabot keeps them current (.github/dependabot.yml).

FROM python:3.15-slim@sha256:2da676046630d7bf14c6764ae1143dc7fa0377a53f94bf5a642f0b214aca5bce AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt requirements-webui.in requirements-webui.txt requirements.yml ./
# Exact versions with hashes (requirements-webui.txt), wheels only: what the image runs was reviewed.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: -r requirements-webui.txt
# The collections ansible.cfg looks for in collections/ (collections_path) next to it.
RUN /opt/venv/bin/ansible-galaxy collection install -r requirements.yml -p /opt/collections \
 && find /opt/collections -type d -name tests -prune -exec rm -rf {} + \
 && /opt/venv/bin/ansible-galaxy collection list -p /opt/collections > /opt/collections/VERSIONS.txt

FROM python:3.15-slim@sha256:2da676046630d7bf14c6764ae1143dc7fa0377a53f94bf5a642f0b214aca5bce AS runtime
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
 && useradd --uid 10001 --user-group --create-home --home-dir /home/usas --shell /usr/sbin/nologin usas \
 && install -d -m 0700 -o usas -g usas /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/collections /app/collections
COPY ansible.cfg LICENSE requirements.txt requirements.yml /app/
COPY playbooks /app/playbooks
COPY roles /app/roles
COPY inventory/hosts.example.yml inventory/vault.example.yml /app/inventory/
COPY webui/usas_web /app/webui/usas_web
COPY LICENSE /usr/share/licenses/ubuntu-server-auto-setup/LICENSE
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/app/webui \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/usas \
    ANSIBLE_CONFIG=/app/ansible.cfg \
    USAS_PROJECT_DIR=/app \
    USAS_DATA_DIR=/data
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
