# openvas-cli AI Agent Guide

This document is for AI agents that need to install, configure, and use `openvas-cli` safely and predictably.

## Goal

Use `openvas-cli` to manage a live OpenVAS / Greenbone instance either:

- locally via Unix socket
- remotely via SSH
- remotely via TLS

For Greenbone Community Edition, prefer the built-in SSH wrapper mode provided by `openvas-cli` instead of relying on `gvm-cli ssh` directly.

---

## 1. Installation

### Prerequisites

Required:

- `python3`
- `gvm-cli`

Helpful local tools:

- `ssh`
- `ssh-keygen`
- `ssh-keyscan`
- `sshpass` *(required only during SSH onboarding bootstrap)*

Install dependencies on Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv ssh sshpass
python3 -m pip install gvm-tools
```

Install `openvas-cli`:

```bash
bash ./install.sh install
```

Check install state:

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

- use `socket` when the CLI runs on the same host as `gvmd`
- use `ssh` for Greenbone Community Edition remote access
- use `tls` only when GMP over TLS is explicitly configured on the server

---

## 4. SSH onboarding behavior

For `ssh` transport, onboarding is designed to minimize future user interaction.

### What onboarding does in SSH mode

1. asks for remote host, port, and SSH username
2. generates a local SSH keypair if missing
3. adds the remote host to `~/.ssh/known_hosts`
4. prompts once for the SSH password
5. installs the generated public key into the remote user's `authorized_keys`
6. stores the SSH identity path in config
7. stores the remote `gvm-cli` path in config
8. stores GMP credentials in config

Default generated key path:

```bash
~/.ssh/openvas_cli_ed25519
```

After onboarding succeeds, normal commands should not need explicit SSH identity arguments.

### Important runtime model

For Greenbone Community Edition, `openvas-cli` uses:

```text
local openvas-cli
  -> local ssh
  -> remote gvm-cli socket
  -> remote gvmd socket
```

It does **not** depend on `gvm-cli ssh` working on the remote CE host.

### Remote requirements for SSH mode

The remote host must have:

- reachable SSH service
- `gvm-cli` available in `PATH`, or a configured explicit path
- access to the `gvmd` Unix socket

Typical socket path:

```bash
/run/gvmd/gvmd.sock
```

---

## 5. Config values agents should know

Common config file:

```bash
~/.config/openvas-cli/openvas-cli.conf
```

Typical SSH config:

```bash
OPENVAS_TRANSPORT="ssh"
OPENVAS_HOST="openvas.example.com"
OPENVAS_PORT="22"
OPENVAS_SSH_USERNAME="scanner"
OPENVAS_SSH_IDENTITY_FILE="/home/user/.ssh/openvas_cli_ed25519"
OPENVAS_REMOTE_GVM_CLI_BIN="gvm-cli"
OPENVAS_SOCKET_PATH="/run/gvmd/gvmd.sock"
OPENVAS_GMP_USERNAME="admin"
OPENVAS_GMP_PASSWORD="..."
```

Typical local socket config:

```bash
OPENVAS_TRANSPORT="socket"
OPENVAS_SOCKET_PATH="/run/gvmd/gvmd.sock"
OPENVAS_GMP_USERNAME="admin"
OPENVAS_GMP_PASSWORD="..."
```

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

## 8. Credential management

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

### If `doctor` fails in SSH mode

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

### If TLS fails

Typical causes:

- wrong port
- wrong CA file
- self-signed cert not trusted by client

### If credential deletion fails

The credential is likely still in use by one or more targets.

---

## 10. Safety guidance for agents

- prefer `openvas-cli onboard` before any first use on a new machine
- prefer saved config over repeatedly passing secrets on the command line
- do not assume remote CE supports `gvm-cli ssh`
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
