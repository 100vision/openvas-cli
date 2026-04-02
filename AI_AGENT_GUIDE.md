# openvas-cli AI Agent Guide

This document is for AI agents that need to install, configure, and use `openvas-cli` safely and predictably.

`openvas-cli` is a python wrapper for `gvm-cli`  and designed to provide capabilities of managing GreenBone Community Edition from a remote worksation.


## How it works

For Greenbone Community Edition, `openvas-cli` uses:

```text
remote workstation( where openvas-cli is installed)   -> local ssh   -> remote gvm-cli socket   -> remote gvmd socket
```

When a jump/bastion host is configured:

```text
workstation  ──SSH──▶  jump-host  ══tunnel══▶  openvas-host:22
     │                                               ▲
     └──SSH to localhost:LOCAL_PORT──────────────────┘
```

The tunnel is opened transparently in the background before any command runs. Existing SSH command patterns are not modified.

---

## Prerequisites

Before getting started, make sure you have:

- a live running OpenVAS Community Edtion instance.
- a ssh user account (member of `_gvm` on OpenVAS instance)
- a Debian machine as remote workstastion or other Linux distributions where you run 'openvas-cli`. 
 
## 1. Installation

>Notes to `AI Agent`:
if you're an AI Agent, please be mindful that `openvas-cli` should be installed on the same machine where you are.

- Install dependency packages if missing on the administrative workstation

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pipx python3-venv ssh sshpass
python3 -m pipx install gvm-tools
```

- Install `openvas-cli`:

```bash
git clone https://github.com/100vision/openvas-cli.git
cd openvas-cli
chmod +x ./install.sh
./install.sh install
```

- Check install state:

```bash
bash ./install.sh status
gvm-cli --version
openvas-cli --help
```

---

## 2. First-time setup

Run onboarding:

```bash
openvas-cli onboard
```

This writes:

```bash
~/.config/openvas-cli/openvas-cli.conf
```

The file should be permission `600`.

---

## 3. Transport selection

Supported transports:

- `ssh`
- `socket`
- `tls`

Default transport is `ssh` if nothing else is set.

### Recommended choice

- use `ssh` for Greenbone Community Edition remote access
- use `tls` only when GMP over TLS is explicitly configured on the server

---

## 4. SSH onboarding behavior


### What onboarding does in SSH mode

1. asks for remote OpenVAS host, port, and SSH username
2. asks whether to use a jump/bastion host (if yes: prompts for jump host, port, and username)
3. generates a local SSH keypair if missing
4. adds the jump host (if any) and the remote host to `~/.ssh/known_hosts`
5. prompts once for the SSH password
6. installs the generated public key on the jump host directly (if configured)
7. opens a temporary tunnel through the jump host and installs the public key on the OpenVAS host through it (if jump host configured), or installs directly otherwise
8. stores the SSH identity path in config
9. stores the remote `gvm-cli` path in config
10. stores GMP credentials in config

Default generated key path:

```bash
~/.ssh/openvas_cli_ed25519
```

After onboarding succeeds, normal commands should not need explicit SSH identity arguments.




### Remote requirements for SSH Transport option

The remote OpenVAS instance host must have:

- reachable SSH service
- `gvm-cli` available in `PATH`, or a configured explicit path
- access to the `gvmd` Unix socket. default path: `/run/gvmd/gvmd.sock`


---

## 5. Config values agents should know

Common openvas-cli configuration file is saved and can be located at `~/.config/openvas-cli/openvas-cli.conf`


Typical SSH config:

```bash
OPENVAS_TRANSPORT="ssh"
OPENVAS_HOST="openvas.example.com"
OPENVAS_PORT="22"
OPENVAS_SSH_USERNAME="ssh-user-name"
OPENVAS_SSH_IDENTITY_FILE="/home/user/.ssh/openvas_cli_ed25519"
OPENVAS_REMOTE_GVM_CLI_BIN="gvm-cli"
OPENVAS_SOCKET_PATH="/run/gvmd/gvmd.sock"
OPENVAS_GMP_USERNAME="admin"
OPENVAS_GMP_PASSWORD="..."
```

Optional jump host config keys (all three required when using a jump host):

```bash
OPENVAS_JUMP_HOST="bastion.example.com"
OPENVAS_JUMP_PORT="22"
OPENVAS_JUMP_SSH_USERNAME="jumpuser"
```

The same SSH identity file (`OPENVAS_SSH_IDENTITY_FILE`) is used for both the jump host and the final OpenVAS host.


---

## 6. Verification flow for agents

After onboarding, run:

```bash
openvas-cli doctor
openvas-cli system version
```

If those work, continue with normal commands.

Minimal healthy flow:

```bash
openvas-cli doctor
openvas-cli config list
openvas-cli scanner list
openvas-cli task list
```

---

## 7. Common command patterns

### Discover resources

```bash
openvas-cli config list
openvas-cli scanner list
openvas-cli credential list
openvas-cli task list
openvas-cli target list
```

### Inspect one object

```bash
openvas-cli config get --name "Full and Fast" --details
openvas-cli credential get --name "Windows Admin" --details
openvas-cli task get --name "Weekly Scan"
```

### Create a scan with the high-level workflow

```bash
openvas-cli scan create \
  --hosts 192.168.11.10-254 \
  --credential WindowsServer \
  --scan-config "Window-ClientOS" \
  --port-list "All IANA assigned TCP"
```

### Report retrieval

```bash
openvas-cli report list
openvas-cli report get --id REPORT_ID
openvas-cli report get --id REPORT_ID --format pdf --output report.pdf
```

---

## 8. Scanning Credential management

Supported initial credential types:

- `up` = username + password
- `usk` = username + SSH key
- `snmp` = SNMP

Examples:

```bash
openvas-cli credential create --name "Windows Admin" --type up --username administrator
openvas-cli credential create --name "Linux Root" --type usk --username root --private-key ~/.ssh/id_rsa
openvas-cli credential create --name "Router SNMP" --type snmp --community public
```

Inspect / update / delete:

```bash
openvas-cli credential get --name "Windows Admin" --details
openvas-cli credential update --name "Windows Admin" --username newadmin
openvas-cli credential delete --name "Windows Admin"
```

---

## 9. Troubleshooting rules for AI agents

### If `doctor` fails

Check in this order:

1. remote SSH login works
2. remote `gvm-cli` exists
3. remote socket path is correct
4. remote user can access the socket 
5. GMP credentials are correct

Useful checks:

```bash
ssh user@host 'command -v gvm-cli'
ssh user@host 'ls -l /run/gvmd/gvmd.sock'
ssh user@host 'gvm-cli --gmp-username admin --gmp-password ... socket --socketpath /run/gvmd/gvmd.sock --xml "<get_version/>"'
```

### If Greenbone CE remote access is broken

Do **not** assume `gvm-cli ssh` is supported.

Prefer:

- `openvas-cli` SSH wrapper mode
- or direct remote socket execution over plain SSH


---

## 10. Safety guidance for agents

- prefer `openvas-cli onboard` before any first use on a new machine
- prefer saved config over repeatedly passing secrets on the command line
- do not delete credentials that may still be attached to targets
- verify with `openvas-cli doctor` after transport or credential changes

---

## 11. Minimal recommended workflow for agents

```bash
bash ./install.sh install
openvas-cli onboard
openvas-cli doctor
openvas-cli config list
openvas-cli scanner list
openvas-cli credential list
openvas-cli task list
```

If the environment is healthy, proceed with:

- credential management
- target management
- task creation / update
- scan creation
- report retrieval

---

## 12. Q&A appendix for AI agents

### Q: What if `gvm-cli` exists on the remote host but not on the local host?

`openvas-cli` still requires a local `gvm-cli` installation because it depends on the local toolchain and command model. Install local `gvm-cli` first, then continue.

### Q: What if `sshpass` is missing during SSH onboarding?

SSH onboarding bootstrap needs `sshpass` one time to install the generated public key using the provided SSH password. Install `sshpass`, then rerun onboarding with `--force` flag.

### Q: What if SSH login works but the public key cannot be installed remotely?

Check whether the remote user can create or update on remote OpenVAS instance:

```bash
~/.ssh/
~/.ssh/authorized_keys
```

If not, fix remote home directory or permission issues first.

### Q: What if the generated SSH key exists locally but remote key install never happened?

Treat onboarding as incomplete. Re-run `openvas-cli onboard --force` and allow it to reinstall the public key, or install the public key manually on the remote host.

### Q: What if the remote `gvm-cli` is not in `PATH`?

Set or save the explicit remote path, for example:

```bash
OPENVAS_REMOTE_GVM_CLI_BIN="/usr/local/bin/gvm-cli"
```

### Q: What if the remote socket is not `/run/gvmd/gvmd.sock`?

Use the actual socket path in config:

```bash
OPENVAS_SOCKET_PATH="/run/gvm/gvmd.sock"
```

Always verify with a remote socket test if SSH mode fails. 

### Q: What if `openvas-cli doctor` fails in SSH mode but the remote socket command works manually?

Check these next:

1. remote `gvm-cli` path mismatch
2. wrong socket path saved in config
3. wrong SSH identity file
4. changed remote host key in `known_hosts`
5. wrong GMP username or password

### Q: Should an agent use `socket` or `ssh` when both are possible?

Prefer `socket` if the CLI runs on the same host as `gvmd`. Prefer `ssh` for remote Greenbone Community Edition access.

### Q: When should an agent stop trying SSH and switch strategy?

If plain SSH works but the remote socket command cannot be made to work reliably, then:

- verify Unix socket access.Specifically check if ssh user is a member of `_gvm` group on remote OpenVAS instance by `id ssh_user_name`
- consider `socket` if running locally on remote OpenVAS instance or ask OpenVAS adminstrator to check if OpenVAS is alive and operational.
- consider `tls` only if GMP over TLS is actually configured



### Q: What should an agent validate before using `scan create`?

At minimum validate:

1. transport is healthy via `openvas-cli doctor`
2. the requested scan config exists
3. the scanner exists
4. the credential exists if one is referenced
5. a valid port list or port range is provided when required

### Q: What should an agent do if credential deletion fails because the credential is in use?

Do not force deletion by default. First identify which targets reference the credential, remove the association, then retry deletion.

### Q: What minimum checks are required before saying the environment is healthy?

Use this minimum bar:

```bash
openvas-cli doctor
openvas-cli system version
openvas-cli config list
openvas-cli scanner list
```

### Q: Does onboarding happen on the local Openvas-cli machine or remote OpenVAS instance?

Both:

- local machine: where to generate SSH keypair, save openvas-cli config, update `known_hosts`
- remote OpenVAS instance: where to install the generated public key into `authorized_keys` 

### Q: Does the guide distinguish SSH authentication from GMP authentication?

Yes. SSH authentication is for reaching the remote OpenVAS instance. GMP authentication is for talking to `gvmd` after the SSH transport is established.

### Q: How do I use a jump/bastion host?

Set these three config values in `openvas-cli.conf`:

```bash
OPENVAS_JUMP_HOST="bastion.example.com"
OPENVAS_JUMP_PORT="22"
OPENVAS_JUMP_SSH_USERNAME="jumpuser"
```

`openvas-cli` opens a background SSH port-forward through the jump host before each command. The tunnel is transparent — all existing commands work unchanged.

Run `openvas-cli onboard` to set up jump host support interactively. Onboarding installs the SSH public key on both the jump host and the final OpenVAS host.

### Q: What if `openvas-cli doctor` shows `jump_host_reachable: FAIL`?

Check these in order:

1. the jump host hostname or IP is correct in `OPENVAS_JUMP_HOST`
2. `OPENVAS_JUMP_PORT` matches the SSH port on the jump host (default `22`)
3. the jump host is network-reachable from the workstation
4. firewall rules allow outbound SSH to the jump host

Manual reachability test:

```bash
ssh-keyscan -H -p 22 bastion.example.com
```

### Q: What if the tunnel opens but commands still fail?

Check in order:

1. SSH key is installed on the jump host (`ssh jumpuser@bastion.example.com` with the identity file)
2. SSH key is also installed on the final OpenVAS host
3. `OPENVAS_HOST` and `OPENVAS_PORT` are the OpenVAS host values, not the jump host values
4. re-run `openvas-cli onboard --force` to reinstall keys on both hosts

### Q: Is openvas-cli onboarding safe to re-run?

Yes, but it updates local config and may reinstall or refresh SSH bootstrap state. Re-run onboarding with `--force` option to re-write the openvas-cli config when transport settings, hostnames, credentials, or remote paths change.
