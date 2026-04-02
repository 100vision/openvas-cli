# openvas-cli

A command-line tool to manage a live OpenVAS instance either locally or remotely — Python wrapper around native `gvm-cli` from [Greenbone](https://greenbone.github.io/gvm-tools/index.html).

## Features

- Remote OpenVAS management via SSH wrapper (no `gvm-cli ssh` required)
- Jump host / bastion transparent tunneling via SSH ControlMaster
- Automated SSH key onboarding (generates keypair, installs on remote host)
- Json-formatted output
- AI Agent friendly


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

# 1. Onboard (first-time setup — prompts for host, credentials, SSH key install)
openvas-cli onboard

# 2. Verify Readiness for connectivity and runtime environment.
openvas-cli doctor

# 3. Run your first scan
openvas-cli config list --details
openvas-cli credential list
openvas-cli scan create --hosts 192.168.11.10-254 \
  --credential WindowsServer \
  --scan-config "Window-ClientOS" \
  --port-list "All IANA assigned TCP"
```

>Tip:`onboard` saves config to `~/.config/openvas-cli/openvas-cli.conf` (mode `600`), generates an SSH keypair, and installs the public key on the remote host.
>See [AI_AGENT_GUIDE.md](AI_AGENT_GUIDE.md) §4 for a detailed walkthrough of onboarding behavior.

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



## Common Examples

```bash

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

