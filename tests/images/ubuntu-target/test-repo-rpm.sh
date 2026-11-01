#!/bin/sh
# Builds the signed dnf repositories the updates tests upgrade from on the RHEL family (tests/updates.sh), as
# test-repo.sh does for apt: small packages that do nothing, an old version of each to install first, and newer
# ones in two repositories, test-updates and test-security. Every package and both repositories' metadata
# (repomd.xml) are signed with a key made here; test-security carries an updateinfo.xml with a security advisory,
# as Rocky Linux's and AlmaLinux's errata do. The image keeps only the public key (gpgcheck and repo_gpgcheck
# on); the private key stays in the build stage.
#   usas-test-tool     1.0 -> 1.1 (updates) -> 1.2 (security advisory, and newly requires usas-test-newdep)
#   usas-test-extra    1.0 -> 1.1 (updates only, so a security-only run leaves it)
#   usas-test-kernel   1.0 -> 1.1 (updates; listed for dnf needs-restarting, as a kernel is)
#   usas-test-pcr7     1.0 -> 1.1 (security advisory; stands in for shim and the other PCR 7 packages)
#   usas-test-held     1.0 -> 1.1 (updates; locked with dnf versionlock)
set -eu
out="$1"
work="$(mktemp -d)"
mkdir -p "$out/installed" "$out/pool" "$out/test-updates" "$out/test-security"

export GNUPGHOME="$work/gnupg"
mkdir -m 0700 "$GNUPGHOME"
# RSA: every rpm of the family verifies it (EL 9's crypto policy refuses SHA-1, which gpg doesn't use here).
gpg --batch --quiet --passphrase '' --quick-gen-key 'USAS updates test archive <test@example.invalid>' rsa3072 sign never
gpg --batch --armor --export >"$out/RPM-GPG-KEY-usas-test"
key="$(gpg --batch --with-colons --list-keys | awk -F: '/^fpr/ {print $10; exit}')"

package() { # name version [requires]
  spec="$work/$1-$2.spec"
  {
    printf 'Name: %s\nVersion: %s\nRelease: 1\nSummary: test package for the updates playbook\n' "$1" "$2"
    printf 'License: MIT\nBuildArch: noarch\n'
    if [ -n "${3:-}" ]; then printf 'Requires: %s\n' "$3"; fi
    printf '%%description\nTest package for the updates playbook.\n'
    printf '%%install\nmkdir -p %%{buildroot}/usr/share/doc/%s\n' "$1"
    printf 'echo "%s %s" >%%{buildroot}/usr/share/doc/%s/version\n' "$1" "$2" "$1"
    printf '%%files\n/usr/share/doc/%s/version\n' "$1"
  } >"$spec"
  rpmbuild -bb --quiet --define "_topdir $work/rpmbuild" "$spec" >/dev/null
  mv "$work/rpmbuild/RPMS/noarch/$1-$2-1.noarch.rpm" "$out/pool/"
}

for p in tool extra kernel pcr7 held; do package "usas-test-$p" 1.0; done
mv "$out"/pool/*-1.0-1.noarch.rpm "$out/installed/"
package usas-test-newdep 1.0
package usas-test-tool 1.1
package usas-test-tool 1.2 usas-test-newdep
package usas-test-extra 1.1
package usas-test-kernel 1.1
package usas-test-pcr7 1.1
package usas-test-held 1.1
rpmsign --define "_gpg_name $key" --addsign "$out"/pool/*.rpm "$out"/installed/*.rpm >/dev/null

for p in tool-1.1 extra-1.1 kernel-1.1 held-1.1; do cp "$out/pool/usas-test-$p-1.noarch.rpm" "$out/test-updates/"; done
for p in tool-1.2 newdep-1.0 pcr7-1.1; do cp "$out/pool/usas-test-$p-1.noarch.rpm" "$out/test-security/"; done
cat >"$work/updateinfo.xml" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<updates>
  <update from="test@example.invalid" status="final" type="security" version="1">
    <id>USAS-TEST-2026:0001</id>
    <title>Security update for the updates tests</title>
    <issued date="2026-01-01 00:00:00"/>
    <severity>Important</severity>
    <description>Test advisory.</description>
    <pkglist>
      <collection short="usas-test">
        <package name="usas-test-tool" version="1.2" release="1" epoch="0" arch="noarch">
          <filename>usas-test-tool-1.2-1.noarch.rpm</filename>
        </package>
        <package name="usas-test-pcr7" version="1.1" release="1" epoch="0" arch="noarch">
          <filename>usas-test-pcr7-1.1-1.noarch.rpm</filename>
        </package>
      </collection>
    </pkglist>
  </update>
</updates>
XML
for repo in test-updates test-security; do createrepo_c --quiet "$out/$repo"; done
modifyrepo_c "$work/updateinfo.xml" "$out/test-security/repodata" >/dev/null
for repo in test-updates test-security; do
  gpg --batch --yes --armor --detach-sign -u "$key" "$out/$repo/repodata/repomd.xml"
done
rm -rf "$out/pool" "$work"
