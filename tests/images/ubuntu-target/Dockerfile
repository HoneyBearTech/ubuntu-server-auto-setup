# Stands in for a VM the Ubuntu installer has just set up: Ubuntu 26.04 with systemd, socket-activated SSH,
# sudo-rs and the admin user with passwordless sudo, as the autoinstall user-data leaves it. D-Bus and
# python3-apt are on every Ubuntu Server; check mode needs them before the baseline has installed anything.
# The package lists are kept, as on an installed server: the apt module judges them by the directory's age, so
# emptied lists from a build minutes ago would count as fresh and never be refreshed.
FROM ubuntu:26.04@sha256:c098726dd9b9733f3e885f8de0ef375206a311b0a317eadc40d347b1115fb944
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv dbus openssh-server python3 python3-apt sudo iproute2 \
      ca-certificates \
 && useradd -m -s /bin/bash -G sudo sysadmin \
 && echo 'sysadmin:test-password' | chpasswd \
 && echo 'sysadmin ALL=(ALL:ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-ansible-sysadmin \
 && chmod 0440 /etc/sudoers.d/90-ansible-sysadmin \
 && install -d -m 0700 -o sysadmin -g sysadmin /home/sysadmin/.ssh \
 && mkdir -p /var/log/installer \
 && echo 'password: "$6$test$hash"' > /var/log/installer/autoinstall-user-data \
 && chmod 0600 /var/log/installer/autoinstall-user-data \
 && systemctl enable ssh.socket
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
