# openvas-cli

A command-line tool to manage a live OpenVAS instance either locally or remotely — Python wrapper around native `gvm-cli` from [Greenbone](https://greenbone.github.io/gvm-tools/index.html).

## Features

- Remote OpenVAS management via SSH wrapper (no `gvm-cli ssh` required)
- Jump host / bastion transparent tunneling via SSH ControlMaster
- Automated SSH key onboarding (generates keypair, installs on remote host)
- Scan lifecycle management: create, start, stop, resume
- Credential management: SSH, Windows/SMB, SNMP
- JSON and compact-JSON output modes (`--json`, `--compact-json`)
- PDF report export (`report get --format pdf`)
- GMP query filtering (`--filter`)

## Architecture

```text
Direct connection:
  workstation  ──SSH:22──▶  openvas-host  ──▶  gvm-cli socket  ──▶  gvmd.sock

Jump host / bastion:
  workstation  ──SSH:22──▶  jump-host  ══tunnel══▶  openvas-host:22
       │                                                 ▲
       └──SSH to localhost:LOCAL_PORT────────────────────┘
           (existing commands see this as a direct connection)
```

## Prerequisites

Before using `openvas-cli`, make sure these are in place:

- `python3` 3.9+
- `gvm-tools` 25.4.9 (provides `gvm-cli`)
- `sshpass` (required only during SSH onboarding)

Install on Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pipx sshpass
python3 -m pipx install gvm-tools
```

## Installation

```bash
cd openvas-cli
chmod +x ./install.sh
./install.sh install
```

Check status, uninstall, or use a custom directory:

```bash
./install.sh status
./install.sh uninstall
OPENVAS_CLI_INSTALL_DIR="$HOME/bin" ./install.sh install
```

## Quick Start

```bash
# 1. Install dependencies and openvas-cli
sudo apt-get install -y python3 python3-venv python3-pipx sshpass
python3 -m pipx install gvm-tools
./install.sh install

# 2. Onboard (first-time setup — prompts for host, credentials, SSH key install)
openvas-cli onboard

# 3. Verify connectivity
openvas-cli doctor

# 4. Run your first scan
openvas-cli config list --details
openvas-cli credential list
openvas-cli scan create --hosts 192.168.11.10-254 \
  --credential WindowsServer \
  --scan-config "Window-ClientOS" \
  --port-list "All IANA assigned TCP"
```

`onboard` saves config to `~/.config/openvas-cli/openvas-cli.conf` (mode `600`), generates an SSH keypair, and installs the public key on the remote host. See [AI_AGENT_GUIDE.md](AI_AGENT_GUIDE.md) §4 for a detailed walkthrough of onboarding behavior.

## Subcommands

| Subcommand | Description |
|---|---|
| `onboard` | First-time setup: collect connection details, generate SSH keys, install public key on remote host |
| `doctor` | Verify connectivity, SSH reachability, remote socket, and GMP credentials |
| `system version` | Print the remote GVM / OpenVAS version |
| `target list\|get\|create\|update` | Manage scan targets (hosts, port lists, credentials) |
| `task list\|get\|create\|update\|start\|stop\|resume` | Manage scan tasks and control their lifecycle |
| `report list\|get` | List or retrieve scan reports (supports PDF export) |
| `config list\|get` | Discover available scan configurations |
| `scanner list` | List available scanners |
| `credential list\|get\|create\|update\|delete` | Manage scan credentials (SSH, Windows, SNMP) |
| `report-format list` | List available report formats |
| `scan create` | High-level workflow: find/create target + task, then start the scan |

## Connection Modes

SSH is the default transport when `--transport` and `OPENVAS_TRANSPORT` are not set. `openvas-cli` uses plain SSH to run `gvm-cli socket` on the remote host, which avoids the Greenbone Community Edition limitation where `gvm-cli ssh` is not available out of the box. Socket and TLS transports are also supported for local or TLS-configured deployments.

| Transport | When to use |
|---|---|
| `ssh` | Default — remote Greenbone Community Edition access |
| `socket` | Local — when running on the same host as `gvmd` |
| `tls` | When GMP over TLS is explicitly configured on the server |

## Jump Host / Bastion

When `OPENVAS_JUMP_HOST` is set, `openvas-cli` automatically opens a persistent SSH ControlMaster tunnel through the jump host before running any command. The tunnel is reused across invocations (`ControlPersist=10m`) and is transparent to all subcommands.

Add these three keys to `openvas-cli.conf`:

```bash
OPENVAS_JUMP_HOST="bastion.example.com"
OPENVAS_JUMP_PORT="22"
OPENVAS_JUMP_SSH_USERNAME="jumpuser"
```

Run `openvas-cli onboard` — when prompted, answer `yes` to jump host setup. Onboarding installs the SSH public key on both the jump host and the final OpenVAS host. After onboarding, `openvas-cli doctor` includes a `jump_host_reachable` check.

## Configuration Reference

All keys are stored in `~/.config/openvas-cli/openvas-cli.conf`.

| Key | Description | Default |
|---|---|---|
| `OPENVAS_TRANSPORT` | Transport mode: `ssh`, `socket`, or `tls` | `ssh` |
| `OPENVAS_HOST` | Remote OpenVAS hostname or IP | — |
| `OPENVAS_PORT` | SSH port on the OpenVAS host | `22` |
| `OPENVAS_SSH_USERNAME` | SSH login username on the OpenVAS host | — |
| `OPENVAS_SSH_IDENTITY_FILE` | Path to the SSH private key | `~/.ssh/openvas_cli_ed25519` |
| `OPENVAS_REMOTE_GVM_CLI_BIN` | Path to `gvm-cli` on the remote host | `gvm-cli` |
| `OPENVAS_SOCKET_PATH` | Unix socket path for `gvmd` on the remote host | `/run/gvmd/gvmd.sock` |
| `OPENVAS_GMP_USERNAME` | GMP (OpenVAS) login username | — |
| `OPENVAS_GMP_PASSWORD` | GMP (OpenVAS) login password | — |
| `OPENVAS_JUMP_HOST` | Jump / bastion host hostname or IP (optional) | — |
| `OPENVAS_JUMP_PORT` | SSH port on the jump host | `22` |
| `OPENVAS_JUMP_SSH_USERNAME` | SSH login username on the jump host | — |

## Common Examples

```bash
# JSON and compact-JSON output
openvas-cli --json config list
openvas-cli --compact-json config get --name "Window-ClientOS" --details

# Filter tasks by status
openvas-cli --json task list --filter "status~running"

# Filter credentials by name
openvas-cli credential list --filter "name~Windows"

# Export a PDF report
openvas-cli report get --id REPORT_ID --format pdf --output report.pdf
```

Example filtered output:

```json
{
  "tasks": [
    {
      "id": "c343d933-f8ca-452a-8e6b-ee7127592c67",
      "name": "Scan 192.168.20.10-254",
      "status": "Running",
      "progress": "5",
      "config_name": "Window-ClientOS",
      "target_name": "Target 192.168.20.10-254"
    }
  ]
}
```

## Credential Management

Manage scan credentials for authenticated scans.

### Credential Types

| Type | Code | Description |
|------|------|-------------|
| Username + Password | `up` | For Windows/SMB authentication |
| Username + SSH Key | `usk` | For Linux/Unix SSH authentication |
| SNMP | `snmp` | For network devices (v1/v2 community or v3) |

### Create

```bash
# Username + Password (prompts for password)
openvas-cli credential create --name "Windows Admin" --type up --username administrator

# SSH Key (prompts for passphrase if needed)
openvas-cli credential create --name "Linux Root" --type usk --username root --private-key ~/.ssh/id_rsa

# SNMP v1/v2 (community string)
openvas-cli credential create --name "Router SNMP" --type snmp --community public

# SNMP v3 with auth and privacy
openvas-cli credential create --name "SNMP v3" --type snmp \
  --snmp-username user --snmp-auth-password authpass \
  --snmp-auth-protocol sha1 --snmp-priv-password privpass \
  --snmp-priv-protocol aes
```

### Get / List

```bash
openvas-cli credential list
openvas-cli credential get --name "Windows Admin"
openvas-cli credential get --name "Windows Admin" --details
```

### Update

```bash
openvas-cli credential update --name "Windows Admin" --username newadmin
openvas-cli credential update --name "Windows Admin" --password
```

### Delete

```bash
openvas-cli credential delete --name "Windows Admin"
openvas-cli credential delete --name "Windows Admin" --force
```

**Note:** Credentials in use cannot be deleted. Remove them from targets first using `target update`.

## Troubleshooting

Run `openvas-cli doctor` after any configuration change. It checks SSH reachability, the remote socket, GMP credentials, and (if configured) jump host reachability.

Common failure checklist:

1. Remote SSH login works with the configured identity file
2. `gvm-cli` exists on the remote host (`ssh user@host 'command -v gvm-cli'`)
3. Remote socket path matches `OPENVAS_SOCKET_PATH` (`ls -l /run/gvmd/gvmd.sock`)
4. Remote SSH user is a member of the `_gvm` group
5. GMP username and password are correct in config
6. Jump host hostname, port, and SSH key are correct (if using a bastion)

See [AI_AGENT_GUIDE.md](AI_AGENT_GUIDE.md) §9 for a detailed troubleshooting checklist.

## Links

- [AI Agent Guide](AI_AGENT_GUIDE.md) — detailed onboarding behavior, config reference, and troubleshooting for automated agents
- [Greenbone gvm-tools documentation](https://greenbone.github.io/gvm-tools/index.html)

