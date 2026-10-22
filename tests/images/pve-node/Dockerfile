# Stands in for a Proxmox VE node in the integration tests: Debian (which Proxmox VE is built on) with
# root SSH, Python for Ansible and xorriso. The tests use it to build the installer ISO and the seed ISOs.

# A small stand-in for the Ubuntu live-server ISO, with a SHA256SUMS file in Ubuntu's format, signed like
# Ubuntu signs it (SHA256SUMS.gpg) with a test key made here. The private key stays in this build stage;
# the node gets only the public keyring and its fingerprint, which tests/run.sh hands to the role. tampered/
# is a mirror that swapped in another checksum but kept the signature; unsigned/ has no signature at all.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f AS release
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends xorriso gpg gpg-agent \
 && rm -rf /var/lib/apt/lists/*
COPY grub.cfg /srv/iso-tree/boot/grub/grub.cfg
ENV GNUPGHOME=/root/gnupg
RUN mkdir -p /srv/iso-tree/casper /srv/ubuntu/tampered /srv/ubuntu/unsigned /srv/test-signing \
 && echo kernel > /srv/iso-tree/casper/vmlinuz \
 && echo initrd > /srv/iso-tree/casper/initrd \
 && xorriso -as mkisofs -quiet -volid "Ubuntu-Server 26.04.1 LTS amd64" -joliet -rock \
      -output /srv/ubuntu/ubuntu-26.04.1-live-server-amd64.iso /srv/iso-tree \
 && cd /srv/ubuntu && sha256sum --binary ubuntu-26.04.1-live-server-amd64.iso > SHA256SUMS \
 && mkdir -m 700 "$GNUPGHOME" \
 && gpg --batch --quiet --passphrase '' --quick-gen-key 'Integration test signing key <test@invalid>' ed25519 sign never \
 && gpg --batch --quiet --armor --detach-sign --output SHA256SUMS.gpg SHA256SUMS \
 && gpg --export --export-options export-minimal > /srv/test-signing/keyring.gpg \
 && gpg --with-colons --list-keys | awk -F: '$1 == "fpr" { print $10; exit }' > /srv/test-signing/fingerprint \
 && echo "$(echo tampered | sha256sum | cut -d' ' -f1) *ubuntu-26.04.1-live-server-amd64.iso" > tampered/SHA256SUMS \
 && cp SHA256SUMS.gpg tampered/ \
 && cp SHA256SUMS unsigned/

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
ENV DEBIAN_FRONTEND=noninteractive
# The empty package lists are dated in the past: Ansible's apt module judges the cache's age by that
# directory, and would otherwise skip the update the role asks for and find no packages to install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server python3 python3-apt xorriso ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && touch -d 2000-01-01 /var/lib/apt/lists \
 && mkdir -p /run/sshd /root/.ssh /var/lib/vz/template/iso /mnt/pve/synology-nfs/template/iso \
 && chmod 700 /root/.ssh \
 && printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > /etc/ssh/sshd_config.d/10-test.conf
# Proxmox's storage tool, faked: the roles ask it which storages allow ISO images and where they keep them.
COPY --chmod=0755 pvesm /usr/local/bin/pvesm
# xorriso, wrapped to keep a copy of each seed's user-data for the tests to check.
COPY --chmod=0755 xorriso /usr/local/bin/xorriso
# Served over HTTP as the stand-in for releases.ubuntu.com. gpgv isn't installed: the role installs it.
COPY --from=release /srv/ubuntu /srv/ubuntu
COPY --from=release /srv/test-signing /srv/test-signing
EXPOSE 22
CMD ["/bin/sh", "-c", "cd /srv/ubuntu && python3 -m http.server 8080 --bind 127.0.0.1 >/dev/null 2>&1 & exec /usr/sbin/sshd -D -e"]
