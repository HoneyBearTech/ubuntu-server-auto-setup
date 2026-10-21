# Verifying releases

Every release of ubuntu-server-auto-setup is built and published by the
[`release.yml`](../.github/workflows/release.yml) workflow when a version tag is pushed. You can check that
what you downloaded came from that workflow, unchanged:

- each GitHub Release has the **source archive** (`ubuntu-server-auto-setup-<version>.tar.gz`, which
  includes the LICENSE), an **SBOM** in SPDX format (`ubuntu-server-auto-setup-<version>.spdx.json`) and
  **`SHA256SUMS`** covering both;
- `SHA256SUMS` is signed with [cosign](https://docs.sigstore.dev/) "keylessly"
  (`SHA256SUMS.sigstore.json`): the signature is tied to the workflow's GitHub identity through
  [Sigstore](https://www.sigstore.dev/), so there is no long-lived signing key to steal, and every
  signature is recorded in the public Rekor transparency log;
- every file in `SHA256SUMS` has **SLSA build provenance**: a signed attestation, stored by GitHub and
  attached to the release (`ubuntu-server-auto-setup-<version>.provenance.sigstore.json`, the full Sigstore
  bundle, and `ubuntu-server-auto-setup-<version>.intoto.jsonl`, the signed in-toto envelope), saying which
  workflow run built them from which commit;
- the **version tag** in git is signed with the maintainer's SSH key;
- the **container image** `docker.io/honeybeartech/ubuntu-server-auto-setup:<version>` (mirrored to `ghcr.io/honeybeartech/ubuntu-server-auto-setup`; from the first
  release with the web UI on) is signed keylessly with cosign by the same workflow, carries an SBOM and SLSA
  provenance in both registries, and its names and digest are in `images.txt`, which `SHA256SUMS` covers.

This applies to every release from the first one,
[v0.1.0](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases/tag/v0.1.0), which the examples
use. You need [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) 3.0 or later (with
cosign 2.6, add `--new-bundle-format`), and the [GitHub CLI](https://cli.github.com/) for the provenance.

## The release files

Download `SHA256SUMS`, `SHA256SUMS.sigstore.json`, the archive and the SBOM from the
[release page](https://github.com/HoneyBearTech/ubuntu-server-auto-setup/releases), then:

```sh
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-identity-regexp '^https://github\.com/HoneyBearTech/ubuntu-server-auto-setup/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum -c SHA256SUMS    # on macOS: shasum -a 256 -c SHA256SUMS
```

The first command proves `SHA256SUMS` came from the release workflow on a version tag; the second, that
the archive and the SBOM match it.

## Build provenance

```sh
gh attestation verify ubuntu-server-auto-setup-0.1.0.tar.gz --repo HoneyBearTech/ubuntu-server-auto-setup \
  --signer-workflow HoneyBearTech/ubuntu-server-auto-setup/.github/workflows/release.yml
```

It prints the verified attestation, including the commit and the workflow run. To verify offline, add
`--bundle ubuntu-server-auto-setup-0.1.0.provenance.sigstore.json`. The same works for the SBOM.

## The container image

Check the image's signature before you run it (`start.ps1 -Verify` does the same on Windows). The examples use
0.3.0; use the version you pull, or `latest`.

```sh
cosign verify docker.io/honeybeartech/ubuntu-server-auto-setup:0.3.0 \
  --certificate-identity-regexp '^https://github\.com/HoneyBearTech/ubuntu-server-auto-setup/\.github/workflows/release\.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

It proves the image was pushed by the release workflow on a version tag. Its build provenance is also a
GitHub attestation:

```sh
gh attestation verify oci://docker.io/honeybeartech/ubuntu-server-auto-setup:0.3.0 \
  --repo HoneyBearTech/ubuntu-server-auto-setup \
  --signer-workflow HoneyBearTech/ubuntu-server-auto-setup/.github/workflows/release.yml
```

To pin exactly the image a release published, use the digest in that release's `images.txt` (after
checking `SHA256SUMS` as above): `docker pull honeybeartech/ubuntu-server-auto-setup@sha256:...`. The same
commands work for the mirror: replace `docker.io/honeybeartech/ubuntu-server-auto-setup` with `ghcr.io/honeybeartech/ubuntu-server-auto-setup`.
The image's SBOM (SPDX, of everything in the image) and its SLSA provenance are attached to it as
attestations; `docker buildx imagetools inspect <image> --format '{{ json .SBOM }}'` (or `.Provenance`)
shows them.

## The git tag

If you use a clone instead of the archive, check the tag. The maintainer signs version tags with an SSH key
whose public half is in [`.github/allowed_signers`](../.github/allowed_signers):

```sh
git clone https://github.com/HoneyBearTech/ubuntu-server-auto-setup.git && cd ubuntu-server-auto-setup
git -c gpg.ssh.allowedSignersFile=.github/allowed_signers tag -v v0.1.0
```

It should print `Good "git" signature for 31805425+HoneyBearTech@users.noreply.github.com`. Check
`.github/allowed_signers` against the key published at <https://github.com/HoneyBearTech.keys> or in an
earlier release, rather than trusting the copy in the same checkout alone.

## What the SBOM lists

The SBOM is generated by [syft](https://github.com/anchore/syft) from the source tree: the Python
packages declared in `requirements*.txt` (as declared there, so with version ranges rather than exact
versions) and the GitHub Actions the workflows use. Ansible collections from `requirements.yml`
are installed by `ansible-galaxy` at `make deps` time; their versions are the ones `ansible-galaxy
collection list` shows on your control node.

## If a check fails

Don't use that file. Download it again; if the check still fails, report it privately as a security issue
([SECURITY.md](../SECURITY.md)).
