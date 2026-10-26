#!/bin/sh
# Builds the signed apt repository the updates tests upgrade from (tests/updates.sh): small packages that do
# nothing, an old version of each to install first, and newer ones in two suites, test-updates and
# test-security, whose Release files are signed with a key made here. The image keeps only the public key
# (never [trusted=yes]); the private key stays in the build stage.
#   usas-test-tool     1.0 -> 1.1 (updates) -> 1.2 (security, and newly depends on usas-test-newdep)
#   usas-test-extra    1.0 -> 1.1 (updates only, so a security-only run leaves it)
#   usas-test-kernel   1.0 -> 1.1 (updates; its postinst asks for a reboot, as a kernel's does on Ubuntu)
#   usas-test-pcr7     1.0 -> 1.1 (security; stands in for shim-signed and the other PCR 7 packages)
#   usas-test-held     1.0 -> 1.1 (updates; held)
#   usas-test-phased   1.0 -> 1.1 (updates; phased at 0 %, so apt never offers it)
set -eu
out="$1"
work="$(mktemp -d)"
mkdir -p "$out/installed" "$out/pool" "$out/test-updates" "$out/test-security"

package() { # name version [depends] [postinst]
  dir="$work/$1-$2"
  mkdir -p "$dir/DEBIAN" "$dir/usr/share/doc/$1"
  printf '%s %s\n' "$1" "$2" >"$dir/usr/share/doc/$1/version"
  {
    printf 'Package: %s\nVersion: %s\nArchitecture: all\nMaintainer: Test <test@example.invalid>\n' "$1" "$2"
    [ -n "${3:-}" ] && printf 'Depends: %s\n' "$3"
    printf 'Description: test package for the updates playbook\n'
  } >"$dir/DEBIAN/control"
  if [ -n "${4:-}" ]; then printf '#!/bin/sh\n%s\n' "$4" >"$dir/DEBIAN/postinst"; chmod 0755 "$dir/DEBIAN/postinst"; fi
  dpkg-deb --root-owner-group -Zgzip --build "$dir" "$out/pool/$1_$2_all.deb" >/dev/null
}

# shellcheck disable=SC2016 # the postinst expands $1, not this script
reboot='if [ "$1" = configure ]; then touch /var/run/reboot-required; echo usas-test-kernel >>/var/run/reboot-required.pkgs; fi'
for p in tool extra kernel pcr7 held phased; do package "usas-test-$p" 1.0; done
mv "$out"/pool/*_1.0_all.deb "$out/installed/"
package usas-test-newdep 1.0
package usas-test-tool 1.1
package usas-test-tool 1.2 usas-test-newdep
package usas-test-extra 1.1
package usas-test-kernel 1.1 "" "$reboot"
package usas-test-pcr7 1.1
package usas-test-held 1.1
package usas-test-phased 1.1

export GNUPGHOME="$work/gnupg"
mkdir -m 0700 "$GNUPGHOME"
gpg --batch --quiet --passphrase '' --quick-gen-key 'USAS updates test archive <test@example.invalid>' ed25519 sign never
gpg --batch --export >"$out/keyring.gpg"

suite() { # name, then the packages' file names
  name="$1"; shift
  for f in "$@"; do cp "$out/pool/$f" "$out/$name/"; done
  (cd "$out/$name" && apt-ftparchive packages . >Packages)
  if [ "$name" = test-updates ]; then
    # apt holds back an update phased at 0 % (Ubuntu's phased updates).
    awk '/^Package: usas-test-phased$/ {p=1} {print} p && /^Version:/ {print "Phased-Update-Percentage: 0"; p=0}' \
      "$out/$name/Packages" >"$work/Packages" && mv "$work/Packages" "$out/$name/Packages"
  fi
  gzip -9nk "$out/$name/Packages"
  (cd "$out/$name" && apt-ftparchive -o APT::FTPArchive::Release::Origin=USAS-Test \
    -o APT::FTPArchive::Release::Label=USAS-Test -o APT::FTPArchive::Release::Suite="$name" \
    -o APT::FTPArchive::Release::Codename="$name" release . >Release)
  gpg --batch --yes --clearsign -o "$out/$name/InRelease" "$out/$name/Release"
}
suite test-updates usas-test-tool_1.1_all.deb usas-test-extra_1.1_all.deb usas-test-kernel_1.1_all.deb \
  usas-test-held_1.1_all.deb usas-test-phased_1.1_all.deb
suite test-security usas-test-tool_1.2_all.deb usas-test-newdep_1.0_all.deb usas-test-pcr7_1.1_all.deb
rm -rf "$out/pool" "$work"
