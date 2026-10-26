# Stands in for a VM the Ubuntu installer has just set up: Ubuntu with systemd, SSH (socket-activated from
# 24.04 on), sudo-rs on 26.04 and the admin user with passwordless sudo, as the autoinstall user-data leaves
# it. D-Bus and python3-apt are on every Ubuntu Server; check mode needs them before the baseline has installed
# anything.
# The package lists are kept, as on an installed server: the apt module judges them by the directory's age, so
# emptied lists from a build minutes ago would count as fresh and never be refreshed.
#
# OS picks the release (ubuntu-26.04, ubuntu-24.04 or ubuntu-22.04). The integration tests build the "target"
# stage; the updates tests build "updates", which adds a signed apt repository with updates to install
# (test-repo.sh) and a boot ID that changes when the container restarts.
ARG OS=ubuntu-26.04
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944 AS ubuntu-26.04
FROM ubuntu:24.04@sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac AS ubuntu-24.04
FROM ubuntu:22.04@sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956 AS ubuntu-22.04

# hadolint ignore=DL3006
FROM ${OS} AS target
ENV DEBIAN_FRONTEND=noninteractive
# hadolint ignore=DL3008
RUN . /etc/os-release \
 && if [ "$VERSION_ID" = 26.04 ]; then sudo_rs=sudo-rs; else sudo_rs=; fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus openssh-server python3 python3-apt sudo $sudo_rs \
      iproute2 ca-certificates \
 && useradd -m -s /bin/bash -G sudo sysadmin \
 && echo 'sysadmin:test-password' | chpasswd \
 && echo 'sysadmin ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-ansible-sysadmin \
 && chmod 0440 /etc/sudoers.d/90-ansible-sysadmin \
 && install -d -m 0700 -o sysadmin -g sysadmin /home/sysadmin/.ssh \
 && mkdir -p /var/log/installer \
 && echo 'password: "$6$test$hash"' > /var/log/installer/autoinstall-user-data \
 && chmod 0600 /var/log/installer/autoinstall-user-data \
 && if [ "$VERSION_ID" = 22.04 ]; then systemctl enable ssh; else systemctl enable ssh.socket; fi
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]

# hadolint ignore=DL3006
FROM ${OS} AS test-repo
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends apt-utils gnupg && rm -rf /var/lib/apt/lists/*
COPY test-repo.sh /usr/local/bin/test-repo.sh
RUN /usr/local/bin/test-repo.sh /srv/usas-test-repo

# A server with updates waiting: the test packages' old versions installed (usas-test-held held), Ubuntu's own
# archive replaced by the test repository so nothing comes from the network, and the tools a real server has
# (unattended-upgrades and needrestart, whose reports the playbook reads). The apt timers never fire, so they
# can't take the package lock in the middle of a test.
FROM target AS updates
COPY --from=test-repo /srv/usas-test-repo /srv/usas-test-repo
COPY boot-id.service /etc/systemd/system/usas-boot-id.service
COPY fragile.service /etc/systemd/system/usas-test-fragile.service
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends unattended-upgrades needrestart \
 && dpkg -i /srv/usas-test-repo/installed/*.deb \
 && apt-mark hold usas-test-held \
 && rm -rf /srv/usas-test-repo/installed /etc/apt/sources.list.d/* /var/lib/apt/lists/* \
 && : > /etc/apt/sources.list \
 && mv /srv/usas-test-repo/keyring.gpg /usr/share/keyrings/usas-test-archive.gpg \
 && for suite in test-updates test-security; do \
      echo "deb [signed-by=/usr/share/keyrings/usas-test-archive.gpg] file:/srv/usas-test-repo/$suite ./"; \
    done > /etc/apt/sources.list.d/usas-test.list \
 && mkdir -p /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d \
 && printf '[Timer]\nOnCalendar=\nOnCalendar=2099-01-01\nPersistent=false\nRandomizedDelaySec=0\n' \
      | tee /etc/systemd/system/apt-daily.timer.d/never.conf > /etc/systemd/system/apt-daily-upgrade.timer.d/never.conf \
 && systemctl enable usas-boot-id.service \
 && apt-get update
