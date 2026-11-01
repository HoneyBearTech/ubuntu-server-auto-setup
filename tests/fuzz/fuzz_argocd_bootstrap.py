#!/usr/bin/env python3
"""Fuzz target: the Argo CD bootstrap's settings, its chart values and its root Application (roles/k3s).

Properties, for any repository URL, path, revision, CA and timeout:
- what the role's check ("Check the Argo CD bootstrap's settings") accepts is an https:// URL with no user name or
  password, query, fragment, "." or ".." segment, whitespace or control character; a relative path and a revision
  with no ".." and nothing a shell or Git option could read; and a CA that is PEM certificates only;
- for accepted settings, the root Application and the chart's values read back (as JSON and YAML) as exactly the
  mappings built, with only the fixed keys, the inputs as values and nothing else (the CA keyed by the URL's host);
- what's there of Argo CD is always one of the four states, whatever Helm and kubectl print.
"""

import json
import sys
import urllib.parse

import atheris
import yaml

with atheris.instrument_imports():
    import roles

K3S = roles.filter_plugin("k3s", "k3s")
VARS = roles.role_vars("k3s")
CHECK = roles.assert_conditions("roles/k3s/tasks/check.yml", "Check the Argo CD bootstrap's settings")
CERT = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIU\nAAAA+/==\n-----END CERTIFICATE-----\n"


def word(fdp, alphabet, max_len):
    return roles.from_alphabet(fdp, alphabet, max_len)


# Mostly values the patterns accept, sometimes one with a character they must refuse.
def url(fdp):
    if fdp.ConsumeIntInRange(0, 3):
        host = fdp.PickValueInList(["git.example.test", "github.com", "192.0.2.10", "a-b.c", "x"])
        port = f":{fdp.ConsumeIntInRange(0, 70000)}" if fdp.ConsumeIntInRange(0, 3) == 0 else ""
        path = "".join("/" + word(fdp, "aZ09._~-", 8) for _ in range(fdp.ConsumeIntInRange(0, 3)))
        return fdp.PickValueInList(["https://", "https://", "http://", "https://u:p@"]) + host + port + path
    return word(fdp, "htps:/@?#.ax9 \n-", 40)


def path(fdp):
    if fdp.ConsumeIntInRange(0, 3):
        return "/".join(word(fdp, "aZ09._-", 8) or "." for _ in range(fdp.ConsumeIntInRange(1, 3)))
    return word(fdp, "a./ \n-$`*", 12)


def revision(fdp):
    if fdp.ConsumeIntInRange(0, 3):
        return word(fdp, "aZ09._/-", 20)
    return word(fdp, "a./ \n-:~^@{", 12)


def ca(fdp):
    choice = fdp.ConsumeIntInRange(0, 4)
    if choice == 0:
        return ""
    if choice < 3:
        return CERT * fdp.ConsumeIntInRange(1, 3)
    return CERT.replace("AAAA", word(fdp, "A+/=\n-x ", 8))


def one_input(data):
    fdp = atheris.FuzzedDataProvider(data)
    agents = fdp.ConsumeBool()
    settings = {"k8s_bootstrap_repo_url": url(fdp), "k8s_bootstrap_path": path(fdp),
                "k8s_bootstrap_revision": revision(fdp), "k8s_bootstrap_repo_ca": ca(fdp),
                "k8s_bootstrap_timeout": fdp.PickValueInList([600, "30", "0", "x", 100000])}
    context = {**VARS, **settings, "k3s_helm": {"binaries": {"amd64": {}}}, "ansible_facts": {"architecture": "x86_64"},
               "k8s_server_taint": fdp.ConsumeBool(),
               "k3s_members": [{"role": "server"}] + ([{"role": "agent"}] if agents else [])}
    status = {"rc": fdp.PickValueInList([0, 1]), "stdout": word(fdp, '{}"info:status deployed failed', 40)}
    crd = {"stdout": fdp.PickValueInList(["", "customresourcedefinition.apiextensions.k8s.io/applications.argoproj.io"])}
    assert K3S.k3s_argocd_installed(status, crd) in ("deployed", "retry", "other", "none")
    if not roles.accepted(CHECK, context):
        return

    repo, folder, rev, cert = (settings[k] for k in ("k8s_bootstrap_repo_url", "k8s_bootstrap_path",
                                                     "k8s_bootstrap_revision", "k8s_bootstrap_repo_ca"))
    parts = urllib.parse.urlsplit(repo)
    assert parts.scheme == "https" and parts.username is None and parts.password is None, repo
    assert not parts.query and not parts.fragment and "@" not in repo and "?" not in repo and "#" not in repo, repo
    assert parts.path.startswith("/") and not {".", ".."} & set(parts.path.split("/")), repo
    assert all(c.isprintable() and not c.isspace() for c in repo + folder + rev), (repo, folder, rev)
    assert not folder.startswith("/") and ".." not in folder.split("/"), folder
    assert ".." not in rev and not rev.startswith("-"), rev
    assert agents or not context["k8s_server_taint"], context

    app = K3S.k3s_root_application({"name": "root", "namespace": "argocd", "repo_url": repo, "path": folder,
                                    "revision": rev})
    assert json.loads(json.dumps(app)) == app and yaml.safe_load(yaml.safe_dump(app)) == app, app
    assert set(app) == {"apiVersion", "kind", "metadata", "spec"}, app
    assert app["spec"]["source"] == {"repoURL": repo, "path": folder, "targetRevision": rev}, app

    values = K3S.k3s_argocd_values({"load_balancer": fdp.ConsumeBool(), "repo_url": repo, "repo_ca": cert})
    assert yaml.safe_load(yaml.safe_dump(values)) == values, values
    assert set(values) == {"server", "dex", "configs"}, values
    assert set(values["configs"]) == ({"rbac", "tls"} if cert else {"rbac"}), values
    if cert:
        assert values["configs"]["tls"]["certificates"] == {parts.hostname: cert.strip() + "\n"}, values
        assert cert.lstrip().startswith("-----BEGIN CERTIFICATE-----"), cert


if __name__ == "__main__":
    atheris.Setup(sys.argv, one_input)
    atheris.Fuzz()
