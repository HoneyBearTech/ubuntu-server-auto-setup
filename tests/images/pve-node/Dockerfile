# Stands in for a Proxmox VE node in the integration tests: Debian (which Proxmox VE is built on) with
# root SSH, Python for Ansible and xorriso. The tests use it to build the installer ISO and the seed ISOs.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server python3 python3-apt xorriso ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd /root/.ssh /var/lib/vz/template/iso /mnt/pve/synology-nfs/template/iso /srv/ubuntu \
 && chmod 700 /root/.ssh \
 && printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > /etc/ssh/sshd_config.d/10-test.conf
COPY grub.cfg /srv/iso-tree/boot/grub/grub.cfg
# Proxmox's storage tool, faked: the roles ask it which storages allow ISO images and where they keep them.
COPY --chmod=0755 pvesm /usr/local/bin/pvesm
# A small stand-in for the Ubuntu live-server ISO, served over HTTP with a SHA256SUMS file in Ubuntu's format.
RUN mkdir -p /srv/iso-tree/casper \
 && echo kernel > /srv/iso-tree/casper/vmlinuz \
 && echo initrd > /srv/iso-tree/casper/initrd \
 && xorriso -as mkisofs -quiet -volid "Ubuntu-Server 26.04.1 LTS amd64" -joliet -rock \
      -output /srv/ubuntu/ubuntu-26.04.1-live-server-amd64.iso /srv/iso-tree \
 && cd /srv/ubuntu && sha256sum --binary ubuntu-26.04.1-live-server-amd64.iso > SHA256SUMS
EXPOSE 22
CMD ["/bin/sh", "-c", "cd /srv/ubuntu && python3 -m http.server 8080 --bind 127.0.0.1 >/dev/null 2>&1 & exec /usr/sbin/sshd -D -e"]
