# Pinning the Proxmox node's SSH host key and API certificate

The playbooks reach each Proxmox node in two ways: over **SSH as `pve_ssh_user`**, root by default (to
build the ISOs and write the seed files, through the inventory host named by `pve_ssh_host`) and over **HTTPS to the API** (to create, start
and watch the VMs, at `pve_api_host:pve_api_port`). Both are checked by default: `ansible.cfg` sets
`host_key_checking = true`, and `pve_validate_certs` is `true`. A check only helps if what you trusted the
first time was the real node, though. This page shows how to read the node's SSH host key and the API's
certificate on the node itself, compare them with what the control node sees, and pin them, instead of
trusting whatever answers the first time (trust on first use).

New VMs are a different case: provisioning trusts the key a new VM offers when it first boots (see
[security.md](security.md#what-it-does-not-protect-against)). This page is about the Proxmox nodes, which
exist before the playbooks run and which you can read from a console, and about VMs you didn't create that
the updates playbook updates ([section 4](#4-vms-you-didnt-create-the-updates-playbooks-cluster-inventory)) or the web UI's
VMs page runs the baseline on ([section 5](#5-vms-you-didnt-create-the-vms-page)) (both use the same pins).

The examples use a node named `pve-1` at `192.0.2.10`, with the API reached as `pve-1.example.lan`. Use
your own node names and addresses.

## 1. The node's SSH host key

### Read the fingerprints on the node

Open a shell on the node by a path that doesn't depend on what you are checking: the physical or IPMI
console, or the Proxmox web UI's **Shell** once you have checked its certificate (step 2). Then:

```sh
for key in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$key"; done
```

Each line shows the key's size, its `SHA256:` fingerprint and its type, such as `(ED25519)`. Note the
Ed25519 one at least: it is the key a current OpenSSH client prefers.

### Compare, then add it to `known_hosts`

On the control node, scan the node and print the fingerprints of what it offered:

```sh
ssh-keyscan -t ed25519 192.0.2.10 > pve-1.known_hosts
ssh-keygen -lf pve-1.known_hosts
```

Add the entry only if the fingerprint is exactly the one the console showed:

```sh
ssh-keygen -R 192.0.2.10          # drop an older entry for that address, if there is one
cat pve-1.known_hosts >> ~/.ssh/known_hosts
```

To avoid the network entirely, copy the line from the node's `/etc/ssh/ssh_host_ed25519_key.pub` instead
and write the entry by hand, as `192.0.2.10 ssh-ed25519 AAAA...` (the address, then the key's type and
base64 text, without the comment). Check it with `ssh-keygen -lf` the same way.

The first field must be the address Ansible connects to: the `ansible_host` of the node's entry in
`proxmox_nodes` (the inventory host named by `pve_ssh_host`, which defaults to `pve_node`). If that entry
sets a name instead of an address, scan and write the name. If it sets an `ansible_port` other than 22,
scan with `-p <port>`: the entry then starts with `[192.0.2.10]:<port>`.

### Make an unknown key fail instead of asking

`host_key_checking = true` keeps OpenSSH's own checking on, but OpenSSH's default for a host it doesn't know
is to ask, and a `yes` typed without comparing is trust on first use again. Make an unknown or changed key
fail the run instead, for the Proxmox nodes:

```yaml
    proxmox_nodes:
      vars:
        ansible_user: "{{ pve_ssh_user }}"
        ansible_ssh_common_args: "-o StrictHostKeyChecking=yes"
      hosts:
        pve-1:
          ansible_host: 192.0.2.10
```

Setting it under `all.vars` works too, and is what the web UI and the integration tests do: provisioning
writes each new VM's key to `known_hosts` (`provision_known_hosts_file`) before anything connects to the
VM, and a baseline run against a server this project didn't install then needs that server's key pinned
as well. The same setting can go in `~/.ssh/config` (`Host 192.0.2.10` with `StrictHostKeyChecking yes`).
Don't use `StrictHostKeyChecking=accept-new` or `no`, nor `ANSIBLE_HOST_KEY_CHECKING=False`: they accept a
key nobody compared.

### Clustered nodes

Every node in a cluster has its own host key: pin each node you list in `proxmox_nodes`. When the servers
are spread over several nodes (`pve_node` on a server), `build-installer` and provisioning first reach every
node they need with `StrictHostKeyChecking=yes`, whatever the inventory sets, and stop before building or
creating anything with a message naming each node whose key isn't known (or has changed). The cluster keeps
its nodes' keys in the shared `/etc/pve` file system, so once one node is verified you can read the others'
keys over that verified connection instead of scanning each one:

- `/etc/pve/nodes/<node>/ssh_known_hosts`: each node's own key, on Proxmox VE 8.2 and later;
- `/etc/pve/priv/known_hosts`: the shared file with every node's keys that earlier releases use (each
  node's `/etc/ssh/ssh_known_hosts` points to it). On a cluster upgraded from those releases it may still
  exist without being kept current, so prefer the per-node file where there is one.

```sh
ssh root@192.0.2.10 cat /etc/pve/nodes/pve-2/ssh_known_hosts > pve-2.known_hosts
ssh-keygen -lf pve-2.known_hosts
```

The entries there name the node as the cluster knows it (its node name, sometimes with an address you
don't use), so replace the first field with the address in your inventory before you add the line. If you
can, still compare each node's fingerprint with its own console: the files say what the cluster recorded
when the node joined, not what the node offers today. A reinstalled node has new keys; pin them again the
same way and remove the old entry with `ssh-keygen -R`.

## 2. The API's TLS certificate

`pveproxy`, which serves the API and the web interface on port 8006, uses
`/etc/pve/nodes/<node>/pveproxy-ssl.pem` if there is one (a certificate you uploaded, or one from ACME),
and otherwise `/etc/pve/nodes/<node>/pve-ssl.pem`, which the cluster's own CA (`/etc/pve/pve-root-ca.pem`)
signs. Every API call sends the token, so keep `pve_validate_certs: true` and give the control node the
CA instead of turning the check off.

### Read the fingerprints on the node

In the node's shell (from step 1):

```sh
openssl x509 -in /etc/pve/pve-root-ca.pem -noout -subject -fingerprint -sha256 -enddate
openssl x509 -in /etc/pve/nodes/pve-1/pve-ssl.pem -noout -fingerprint -sha256 -ext subjectAltName
```

Use `pveproxy-ssl.pem` in the second command if the node has one.

### Compare from the control node

```sh
openssl s_client -connect 192.0.2.10:8006 -servername pve-1.example.lan </dev/null 2>/dev/null \
  | openssl x509 -noout -fingerprint -sha256
```

That is the certificate the API presents; it must match the node's. Then copy the CA over the SSH
connection you verified in step 1, and check that its fingerprint matches too:

```sh
scp root@192.0.2.10:/etc/pve/pve-root-ca.pem .
openssl x509 -in pve-root-ca.pem -noout -fingerprint -sha256
```

`pve_api_host` must be one of the names or addresses in the certificate's `subjectAltName` (printed
above). `pve-ssl.pem` lists the node's name and the addresses it had when the certificate was made; to
reach the API by another name, use a listed one or install a certificate that lists it (the node's
**System → Certificates** page).

### Trust the CA for the playbooks

Two parts of the control node check the API's certificate. The `community.proxmox` modules use Python's
`requests`, which trusts the bundle in `REQUESTS_CA_BUNDLE`, or else the `certifi` bundle installed with it
(in a virtual environment, that is not the system's trust store). The playbooks' `ansible.builtin.uri`
calls use Python's own TLS, which trusts the system store or `SSL_CERT_FILE`, and also download your keys
from GitHub, so the bundle must keep the public CAs. Make one bundle with both, and point both variables at
it (the web UI does the same):

```sh
mkdir -p ~/.config/ubuntu-server-auto-setup
cat "$(python3 -m certifi)" pve-root-ca.pem > ~/.config/ubuntu-server-auto-setup/ca-bundle.pem
export SSL_CERT_FILE=~/.config/ubuntu-server-auto-setup/ca-bundle.pem
export REQUESTS_CA_BUNDLE=~/.config/ubuntu-server-auto-setup/ca-bundle.pem
```

Run `python3 -m certifi` inside the virtual environment you installed the dependencies in (step 1 of
[installing.md](installing.md)); on Debian or Ubuntu, `/etc/ssl/certs/ca-certificates.crt` works as the
first file too.

This pins the cluster's CA, not one certificate: the modules can't check a single certificate's
fingerprint. The cluster's CA signs only that cluster's node certificates, so it is the narrow choice.
Renewing a node's `pve-ssl.pem` (`pvecm updatecerts`) needs no change on the control node; replacing the
cluster's CA does, so compare its fingerprint again then. With a certificate from a public CA (ACME), the
default trust already verifies it and no bundle is needed; compare its fingerprint once as above.

## 3. In the web UI (container image)

The web UI does all of this from its forms ([web-ui.md](web-ui.md#walking-through-it)), so you compare instead of
copying files:

- **SSH key** page: for each node in use (the default node and every node a server names), enter its SSH
  address, **scan** its host keys, compare the fingerprints with that node's console `ssh-keygen -lf`
  output, tick the box and choose **Trust these keys**. They are written to
  `/data/ssh/known_hosts` in the data volume, and every connection the UI and the playbooks make uses that
  file with `StrictHostKeyChecking=yes` (the UI sets `ansible_ssh_common_args`,
  `provision_known_hosts_file` and `ssh_known_hosts_file` in its inventory).
- **Proxmox** page: paste `/etc/pve/pve-root-ca.pem` into **Cluster CA certificate**, or fetch it over the
  SSH connection you just verified. The UI shows the CA's SHA-256 fingerprint: compare it with the
  console's `openssl x509 ... -fingerprint -sha256`. It is saved as `/data/pve-ca.pem`, and with the public
  CAs as `/data/ca-bundle.pem`, which `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` point at for the connection
  test and every run.

To give the container a `known_hosts` entry you made and checked elsewhere (as in step 1), append it to the
file in the volume:

```sh
docker exec -i usas sh -c 'umask 077 && mkdir -p /data/ssh && cat >> /data/ssh/known_hosts' < pve-1.known_hosts
```

The first field must be the node's SSH address exactly as the **SSH key** page has it, not a hashed entry
(`ssh-keygen -H`), or the UI won't recognise it. Don't bind-mount a `known_hosts` file into the container,
and never read-only: provisioning adds each new VM's key to it, and the UI replaces the whole file when you
trust or forget a key.

## 4. VMs you didn't create (the updates playbook's cluster inventory)

With the cluster inventory ([updates.md](updates.md#every-vm-on-the-cluster)), a VM that isn't in your
static inventory is reached at whatever address its guest agent reports. So its host key is pinned under a
name made from the cluster and its VM ID, `usas-<cluster>-<vmid>` (`usas-homelab-4093`), not under the
address: every connection passes that name as `HostKeyAlias`. A guest that reports another VM's address
gets a host-key mismatch instead of your sudo password. The cluster's name is the one Proxmox reports
(Datacenter → Summary), or `pve` when the token may not read it, or `cluster:` in the plugin's file. The
run's report shows each VM's alias. There is no trust on first use: a VM with no pin is reported
`unreachable` ("its SSH host key isn't trusted as usas-…") until you pin it.

1. On the VM's console in Proxmox (VM → Console), logged in as any user:

   ```bash
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

2. On the control node, scan the address the report (or `ansible-inventory --host vm-<vmid>`) gives for it,
   store the result under the alias, and compare:

   ```bash
   ssh-keyscan -t ed25519 192.0.2.40 | sed 's/^192\.0\.2\.40 /usas-homelab-4093 /' > vm-4093.known_hosts
   ssh-keygen -lf vm-4093.known_hosts
   ```

   For a port other than 22, use `ssh-keyscan -p <port>`; the alias replaces `[address]:port` as the first
   field.
3. If the two fingerprints match, append `vm-4093.known_hosts` to the `known_hosts` file your runs use (the
   one `ansible_ssh_common_args` names, `~/.ssh/known_hosts` by default; in the web UI's container
   `/data/ssh/known_hosts`).

Servers in your static inventory keep the entry provisioning made for their address (or the one you made as
in step 1), since their address comes from you, not from the guest.

## 5. VMs you didn't create (the VMs page)

The VMs page runs the baseline on VMs of the cluster, including ones this project didn't install. Their
host keys are pinned by **cluster and VM ID**, not by address: the `known_hosts` entry is named
`usas-<cluster>-<vmid>` (the cluster's name from the last connection test, lowercased, or `pve` when it
isn't known), and every connection to that VM passes it as `HostKeyAlias`. A guest agent that reports
another VM's address then only gets `Host key verification failed`. There is no trust on first use:

1. On the VMs page, tick the VM. In its part below the table, choose the address and port, then **Scan the
   host key**.
2. On the VM's console in Proxmox (**the VM → Console**), logged in as any user:

   ```sh
   for f in /etc/ssh/ssh_host_*_key.pub; do ssh-keygen -lf "$f"; done
   ```

3. If every fingerprint the page shows is in that list, tick **The fingerprints match what … shows** and
   **Pin these keys**. If any differs, another machine answered: don't pin.

A server on the Servers page (one this project installed) starts from the key recorded when it was
installed: the first plan for it copies the address entry to its alias, and the audit log says so.

The entry is the same as in [section 4](#4-vms-you-didnt-create-the-updates-playbooks-cluster-inventory), so a VM pinned on the VMs page is pinned for the updates playbook's cluster inventory too, and the other way round.

**Forget the host key** on the VMs page removes the entry (for a VM that was reinstalled: pin the new key
after comparing it). Pinned keys are part of a plan: pinning again after a check run means checking again
before the real run.

## When a key or certificate changes

- `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!` from SSH, or `Host key verification failed` in a VMs
  page run: stop. Check on the node's console whether it
  was reinstalled or its keys regenerated, and compare the new fingerprint. Only then remove the old entry
  (`ssh-keygen -R 192.0.2.10`, or **Forget the host key** in the web UI) and pin the new one.
- `isn't trusted as usas-<cluster>-<vmid>` in an updates report: a VM found on the cluster whose key isn't
  pinned yet, or whose key no longer matches its pin. Pin it as in section 4, after checking on its console
  why it changed.
- `No ED25519 host key is known for <address>` for a server this project installed: a run stopped after the
  install but before it trusted the new VM's key (for example a server with `net_mode: dhcp` whose address
  couldn't be read), and later runs skip the VM because it exists. Either delete the VM (`qm destroy <vmid>
  --purge`, it holds nothing yet) and provision it again, or pin its key as for a node: on the VM's console in
  Proxmox, logged in as the admin user, run `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`; on the control
  node, `ssh-keyscan -t ed25519 <address> > vm.known_hosts` and `ssh-keygen -lf vm.known_hosts`; if the
  fingerprints match, append `vm.known_hosts` to `provision_known_hosts_file` (in the web UI's container,
  `/data/ssh/known_hosts`, as above).
- `CERTIFICATE_VERIFY_FAILED` from the API: don't set `pve_validate_certs: false`. Check the certificate
  and the CA on the node as in step 2: a name missing from `subjectAltName`, an expired certificate or a
  new CA are the usual causes.
