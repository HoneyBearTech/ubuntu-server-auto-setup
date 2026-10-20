## What and why

<!-- What does this change, and why? Link the issue it closes, if any. -->

## How it was tested

<!--
For changes to what a playbook or role does: the commands you ran against a disposable VM, the Proxmox VE
and Ubuntu versions, and the result of a second run (it should report no changes). For SSH, firewall or
user changes: confirm you could still log in afterwards. See CONTRIBUTING.md#test-policy.
-->

## Checklist

- [ ] Every commit is signed off (`git commit -s`), certifying the [DCO](https://developercertificate.org/); see [CONTRIBUTING.md](../CONTRIBUTING.md#developer-certificate-of-origin).
- [ ] `make lint` and `make syntax` pass locally (CI runs the same, plus gitleaks).
- [ ] Changed behaviour was tested as described above (the [test policy](../CONTRIBUTING.md#test-policy)).
- [ ] New or changed variables are documented in the role's `defaults/main.yml`, the README and `docs/interfaces.md`.
- [ ] User-visible changes are in `CHANGELOG.md` under "Unreleased".
- [ ] Nothing here can wipe or reconfigure a VM that already exists, and no secrets, real inventories, host names or addresses are committed.
