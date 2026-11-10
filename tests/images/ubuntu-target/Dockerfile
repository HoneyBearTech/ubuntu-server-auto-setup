# Stands in for a VM the Ubuntu installer has just set up: Ubuntu with systemd, SSH (socket-activated from
# 24.04 on), sudo-rs on 26.04 and the admin user with passwordless sudo, as the autoinstall user-data leaves
# it, with copies of the answers' password hash where the installer and cloud-init leave them, and a stand-in for
# cloud-init's status command (disabled on the cloud-image stand-ins, as the seed leaves it). D-Bus,
# python3-apt and tzdata are on every Ubuntu Server (the Docker images lack tzdata); check mode needs them before
# the baseline has installed anything.
# The package lists are kept, as on an installed server: the apt module judges them by the directory's age, so
# emptied lists from a build minutes ago would count as fresh and never be refreshed.
#
# OS picks the release (ubuntu-26.04, ubuntu-24.04, ubuntu-22.04, debian-13, debian-12, centos-stream-10,
# centos-stream-9, rocky-10, rocky-9, alma-10 or alma-9). Debian and the RHEL family stand in for a VM installed
# from the vendor's cloud image: classic sudo, sshd from its service (no socket activation), the admin user (in
# wheel on the RHEL family) with the sudoers file the cloud-init seed writes. The integration tests build the
# "target" stage; the updates tests build "updates", which adds a signed apt or dnf repository with updates to
# install (test-repo.sh, test-repo-rpm.sh) and a boot ID that changes when the container restarts.
ARG OS=ubuntu-26.04
FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c AS ubuntu-26.04
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 AS ubuntu-24.04
FROM ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11 AS ubuntu-22.04
FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS debian-13
FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc AS debian-12
FROM quay.io/centos/centos:stream10@sha256:511d358446140e9c4ba16451affb103a14237cbf40059b18adeaef6e3080a72b AS centos-stream-10
FROM quay.io/centos/centos:stream9@sha256:99a7748660352b7d87e5689805ddafa6209d6019d0c5b642b5354656dff02be3 AS centos-stream-9
FROM rockylinux/rockylinux:10@sha256:827d37bc128288ccf160ee318bb3cb92d591164cb217e92f8bc61e3982ae1834 AS rocky-10
FROM rockylinux/rockylinux:9@sha256:8101994123cf3d0a8fee517bee7f39e555c7d92bd2d9eb3303cc988a0eeed00f AS rocky-9
FROM almalinux:10@sha256:5a4b41a427c9717be8f4ac2a32aa22efbae5f9124fbcb79a6765394f312acfe8 AS alma-10
FROM almalinux:9@sha256:245f8aab832fe800aa42bdab5fad75ebbd8e0b4c9f24820a83c638b177857ae2 AS alma-9

# hadolint ignore=DL3006
FROM ${OS} AS target
ENV DEBIAN_FRONTEND=noninteractive
# hadolint ignore=DL3008,DL3041
RUN . /etc/os-release \
 && if command -v apt-get >/dev/null; then \
      if [ "$VERSION_ID" = 26.04 ]; then sudo_rs=sudo-rs; else sudo_rs=; fi; \
      apt-get update \
      && apt-get install -y --no-install-recommends systemd systemd-sysv dbus openssh-server python3 python3-apt sudo \
           $sudo_rs iproute2 ca-certificates tzdata \
      && admin_group=sudo; \
    else \
      dnf install -y systemd openssh-server python3 sudo iproute procps-ng passwd \
      && dnf clean all && admin_group=wheel; \
    fi \
 && useradd -m -s /bin/bash -G "$admin_group" sysadmin \
 && echo 'sysadmin:test-password' | chpasswd \
 && echo 'sysadmin ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-ansible-sysadmin \
 && chmod 0440 /etc/sudoers.d/90-ansible-sysadmin \
 && install -d -m 0700 -o sysadmin -g sysadmin /home/sysadmin/.ssh \
 && mkdir -p /var/log/installer \
 && echo 'password: "$6$test$hash"' > /var/log/installer/autoinstall-user-data \
 && chmod 0600 /var/log/installer/autoinstall-user-data \
 && install -d -m 0700 /var/lib/cloud/instances/usas-test \
 && echo 'password: "$6$test$hash"' > /var/lib/cloud/instances/usas-test/user-data.txt \
 && cp /var/lib/cloud/instances/usas-test/user-data.txt /var/lib/cloud/instances/usas-test/user-data.txt.i \
 && cp /var/lib/cloud/instances/usas-test/user-data.txt /var/lib/cloud/instances/usas-test/cloud-config.txt \
 && cp /var/lib/cloud/instances/usas-test/user-data.txt /var/lib/cloud/instances/usas-test/obj.pkl \
 && echo usas-test > /var/lib/cloud/instances/usas-test/instance-id \
 && ln -s /var/lib/cloud/instances/usas-test /var/lib/cloud/instance \
 && mkdir -p /etc/cloud \
 && if [ "$ID" != ubuntu ]; then touch /etc/cloud/cloud-init.disabled; fi \
 && printf '%s\n' '#!/bin/sh' \
      '# Stands in for cloud-init status: disabled after a cloud-image install (the seed writes cloud-init.disabled),' \
      '# done after the Ubuntu installer.' \
      'if [ -e /etc/cloud/cloud-init.disabled ]; then echo "status: disabled"; else echo "status: done"; fi' \
      > /usr/bin/cloud-init \
 && chmod 0755 /usr/bin/cloud-init \
 && if [ "$admin_group" = wheel ]; then systemctl enable sshd; \
    elif [ "$ID" = debian ] || [ "$VERSION_ID" = 22.04 ]; then systemctl enable ssh; else systemctl enable ssh.socket; fi
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]

# hadolint ignore=DL3006
FROM ${OS} AS test-repo
COPY test-repo.sh test-repo-rpm.sh /usr/local/bin/
# hadolint ignore=DL3008,DL3041
RUN if command -v apt-get >/dev/null; then \
      apt-get update && apt-get install -y --no-install-recommends apt-utils gnupg && rm -rf /var/lib/apt/lists/* \
      && /usr/local/bin/test-repo.sh /srv/usas-test-repo; \
    else \
      dnf install -y rpm-build rpm-sign createrepo_c gnupg2 && dnf clean all \
      && /usr/local/bin/test-repo-rpm.sh /srv/usas-test-repo; \
    fi

# A server with updates waiting: the test packages' old versions installed (usas-test-held held), the
# distribution's own repositories replaced by the test repository so nothing comes from the network, and the
# tools a real server has, whose reports the playbook reads: unattended-upgrades and needrestart on apt;
# dnf-automatic, dnf-plugins-core (needs-restarting, told that usas-test-kernel wants a reboot as a kernel does)
# and the versionlock plugin on dnf. The automatic updates' timers never fire, so they can't take the package
# lock in the middle of a test. Every test connection comes from Docker's one address, so OpenSSH 9.8+'s
# per-source penalties (on 26.04) would soon refuse it after the tests' host key scans: off here.
FROM target AS updates
COPY --from=test-repo /srv/usas-test-repo /srv/usas-test-repo
COPY boot-id.service /etc/systemd/system/usas-boot-id.service
COPY fragile.service /etc/systemd/system/usas-test-fragile.service
# A container has no console: a getty that fails at boot would show up in the health check as a unit that
# failed after the update. apt ignores phasing when it can't read a machine ID (a container that has only just
# booted may have none yet): a fixed one makes it hold usas-test-phased back on every run. The RHEL family's
# dnf-automatic installs security updates (every update on CentOS Stream, which publishes no errata), as the
# baseline sets it up; the image imports the test repository's key for its metadata, as a first dnf run with
# the repository does.
# hadolint ignore=DL3008,DL3041
RUN . /etc/os-release \
 && if command -v apt-get >/dev/null; then \
      apt-get update \
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
      && echo 'APT::Machine-ID "8d2a0f3c5b6e4a1f9c7d2e5b3a6f1c4d";' > /etc/apt/apt.conf.d/50usas-test-machine-id \
      && apt-get update; \
    else \
      dnf install -y dnf-automatic dnf-plugins-core python3-dnf-plugin-versionlock \
      && mv /srv/usas-test-repo/RPM-GPG-KEY-usas-test /etc/pki/rpm-gpg/RPM-GPG-KEY-usas-test \
      && rpm --import /etc/pki/rpm-gpg/RPM-GPG-KEY-usas-test \
      && rpm -i /srv/usas-test-repo/installed/*.rpm \
      && dnf versionlock add usas-test-held \
      && dnf clean all && rm -rf /srv/usas-test-repo/installed /etc/yum.repos.d/* \
      && for repo in test-updates test-security; do \
           printf '[usas-%s]\nname=USAS %s\nbaseurl=file:///srv/usas-test-repo/%s\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-usas-test\n' \
             "$repo" "$repo" "$repo" > "/etc/yum.repos.d/usas-$repo.repo"; \
         done \
      && mkdir -p /etc/dnf/plugins/needs-restarting.d \
      && echo usas-test-kernel > /etc/dnf/plugins/needs-restarting.d/usas-test.conf \
      && if [ "$ID" = centos ]; then type=default; else type=security; fi \
      && sed -i -e "s/^upgrade_type = .*/upgrade_type = $type/" -e 's/^apply_updates = .*/apply_updates = yes/' \
           /etc/dnf/automatic.conf \
      && mkdir -p /etc/systemd/system/dnf-automatic.timer.d \
      && printf '[Timer]\nOnCalendar=\nOnCalendar=2099-01-01\nOnBootSec=\nPersistent=false\nRandomizedDelaySec=0\n' \
           > /etc/systemd/system/dnf-automatic.timer.d/never.conf \
      && systemctl enable dnf-automatic.timer \
      && systemctl mask dnf-makecache.timer \
      && dnf -y makecache; \
    fi \
 && systemctl enable usas-boot-id.service \
 && systemctl mask getty@.service serial-getty@.service console-getty.service \
 && ssh-keygen -A >/dev/null \
 && if sshd -T 2>/dev/null | grep -q '^persourcepenalties '; then \
      echo 'PerSourcePenalties no' > /etc/ssh/sshd_config.d/50-usas-test.conf; fi

# A Git server for tests/kubernetes.sh's Argo CD bootstrap: git-server.py serves the test's repository over HTTPS
# (Git's smart HTTP protocol, through git http-backend), as the root Application's repository.
FROM ubuntu-26.04 AS git-server
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 \
 && rm -rf /var/lib/apt/lists/*
COPY git-server.py /usr/local/bin/git-server.py
CMD ["python3", "/usr/local/bin/git-server.py"]
