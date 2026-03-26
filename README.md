<<<<<<< HEAD
# openvas-cli

a Python wrapper around native `gvm-cli` from [GreenBone](https://greenbone.github.io/gvm-tools/index.html)

## Prerequisites

Before using `openvas-cli`, make sure these prerequisites are in place.

1. `python3 3.9+`
2. `gvm-tools 25.4.9`
3. `gvm-cli 25.4.9`

If missing, install these dependency packages for Ubuntu or Debian:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
python3 -m install gvm-tools
```

Quick checks:

```bash
python3 --version
gvm-cli --version
bash ./openvas-cli/install.sh status
```

## Installation

Install, simply run:

```bash
bash ./openvas-cli/install.sh install
```

Check install status:

```bash
bash ./openvas-cli/install.sh status
```

Uninstall the CLI:

```bash
bash ./openvas-cli/install.sh uninstall
```

Use a custom install directory:

```bash
OPENVAS_CLI_INSTALL_DIR="$HOME/bin" bash ./openvas-cli/install.sh install
```


## Onboarding

Upon installation, run：

```bash
openvas-cli onboard` 
```

this is to start first time setup process and take essential information and saves a local config file `~/.config/openvas-cli/openvas-cli.conf`

The file is written with permission `600` on Linux native filesystems.

Supported connection types shown during setup: `[ssh|tls|socket]`

If `socket` is selected, `onboard` tries common gvmd socket paths first. If more than one path is found, it lists them and lets you choose before confirming the final socket path. The final socket path must exist before onboarding can continue.

At the end of onboarding, the CLI prints the saved config file location and suggested next commands.



## Quick Start

Shortest path from install to first scan:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv pipx
python3 -m pip install gvm-tools
export PATH="$HOME/.local/bin:$PATH"

bash ./openvas-cli/install.sh install
openvas-cli onboard
openvas-cli doctor
openvas-cli credential list --filter "name~Windows"
openvas-cli config list --details
openvas-cli scan create --hosts 192.168.11.10-254 --credential WindowsServer --scan-config "Window-ClientOS" --port-range T:1-65535 --port-list "All IANA assigned TCP"

### create a scan task with built-in/preset port-list "All IANA assigned TCP"
openvas-cli scan create --hosts 192.168.11.10-254 --credential WindowsServer --scan-config "Window-ClientOS" --port-list "All IANA assigned TCP"
```

## Subcommands

-  `openvas-cli onboard`
- `openvas-cli doctor`
- `openvas-cli system version`
- `openvas-cli target list|get|create|update`
- `openvas-cli task list|get|create|update|start|stop|resume`
- `openvas-cli report list|get`
- `openvas-cli config list`
- `openvas-cli config get`
- `openvas-cli scanner list`
- `openvas-cli credential list|get|create|update|delete`
- `openvas-cli report-format list`
- `openvas-cli scan create`

Use `openvas-cli config list` to discover available scan configs. The CLI requests the full scan config set with `usage_type=scan` and disables default pagination before you choose one for `--scan-config`. Add `--details`, `--tasks`, or `--preferences` for richer output, or use `openvas-cli config get --name "Window-ClientOS" --details`.

Examples:

```bash
openvas-cli --json config list
openvas-cli --compact-json config get --name "Window-ClientOS" --details
```

## Query Filtering

- filter tasks by task status with a running state.
```bash
 openvas-cli --json task list --filter "status~running"
```
filtered output be like:

```json
{
  "tasks": [
    {
      "config_id": "90247c21-5118-4150-95fe-96763f29a3eb",
      "config_name": "Window-ClientOS",
      "id": "c343d933-f8ca-452a-8e6b-ee7127592c67",
      "last_report_id": "",
      "name": "Scan 192.168.20.10-254",
      "progress": "5",
      "scanner_id": "08b69003-5fc2-4037-a479-93b440211c73",
      "scanner_name": "OpenVAS Default",
      "status": "Running",
      "target_id": "6a732345-786e-4aae-9a95-3f99514669fb",
      "target_name": "Target 192.168.20.10-254"
    }
  ]
}

```

## Usage Notes

### Access to a running GreenBone Openvas instance 

SSH is the default transport if `--transport` and `OPENVAS_TRANSPORT` are not set.

### Scan Tasks

`scan create` is the high level workflow.

It will find or create the target, find or create the task, update mismatched target or task bindings, and start the task unless it is already running.

For new targets, GMP 22.7 needs either `--port-list` or `--port-range`.

### Scan Reporting and Exports

`report get --format pdf` requires `--output` because the PDF is returned as base64 inside XML.

### Credential Management

Manage scan credentials for authenticated scans (SSH, Windows, SNMP).

#### Credential Types

| Type | Code | Description |
|------|------|-------------|
| Username + Password | `up` | For Windows/SMB authentication |
| Username + SSH Key | `usk` | For Linux/Unix SSH authentication |
| SNMP | `snmp` | For network devices (v1/v2 community or v3) |

#### Create Credentials

```bash
# Username + Password (prompts for password)
openvas-cli credential create --name "Windows Admin" --type up --username administrator

# SSH Key (prompts for passphrase if needed)
openvas-cli credential create --name "Linux Root" --type usk --username root --private-key ~/.ssh/id_rsa

# SNMP v1/v2 (community)
openvas-cli credential create --name "Router SNMP" --type snmp --community public

# SNMP v3 with auth and privacy
openvas-cli credential create --name "SNMP v3" --type snmp \
  --snmp-username user --snmp-auth-password authpass \
  --snmp-auth-protocol sha1 --snmp-priv-password privpass \
  --snmp-priv-protocol aes
```

#### Get Credential Details

```bash
# List credentials
openvas-cli credential list

# Get credential by name
openvas-cli credential get --name "Windows Admin"

# Get credential details (excludes secrets)
openvas-cli credential get --name "Windows Admin" --details
```

#### Update Credentials

```bash
# Update username
openvas-cli credential update --name "Windows Admin" --username newadmin

# Update password (prompts)
openvas-cli credential update --name "Windows Admin" --password
```

#### Delete Credentials

```bash
# Delete (fails if in use by targets)
openvas-cli credential delete --name "Windows Admin"

# Force delete (even if in use)
openvas-cli credential delete --name "Windows Admin" --force
```

**Note:** Credentials in use cannot be deleted. Remove them from targets first using `target update`.
=======
# openvas-cli

a Python wrapper around native `gvm-cli` from [GreenBone](https://greenbone.github.io/gvm-tools/index.html)

## Prerequisites

Before using `openvas-cli`, make sure these prerequisites are in place. 


1. `python3 3.9+`
2. `gvm-tools 25.4.9`
3. `gvm-cli 25.4.9`


If missing, install these dependency packages for Ubuntu or Debian:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv
python3 -m install gvm-tools
```

Quick checks:

```bash
python3 --version
gvm-cli --version
bash ./openvas-cli/install.sh status
```

## Installation

Install, simply run:

```bash
bash ./openvas-cli/install.sh install
```

Check install status:

```bash
bash ./openvas-cli/install.sh status
```

Uninstall the CLI:

```bash
bash ./openvas-cli/install.sh uninstall
```

Use a custom install directory:

```bash
OPENVAS_CLI_INSTALL_DIR="$HOME/bin" bash ./openvas-cli/install.sh install
```


## Onboarding

Upon installation, run：

```bash
openvas-cli onboard 
```

> this is to start first time setup process and take essential information and saves a local config file `~/.config/openvas-cli/openvas-cli.conf`. The file is protected with permission `600` on Linux native filesystems.


**Supported connection types**
- `ssh`, default connection option. Useful specially when managing a remote OpenVAS instance. Follow on-screen instructions to enter remote ssh login credentail.
- `tls`
- `socket`, if choosen, work via unix socket with a local OpenVAS instance.



## Quick Start

Quick scan:

```bash
openvas-cli scan create --hosts 192.168.11.10-254 --credential WindowsServer --scan-config "Window-ClientOS" --port-range T:1-65535 --port-list "All IANA assigned TCP"

### create a scan task with built-in/preset port-list "All IANA assigned TCP"
openvas-cli scan create --hosts 192.168.11.10-254 --credential WindowsServer --scan-config "Window-ClientOS" --port-list "All IANA assigned TCP"
```

## Subcommands

-  `openvas-cli onboard`
- `openvas-cli doctor`
- `openvas-cli system version`
- `openvas-cli target list|get|create|update`
- `openvas-cli task list|get|create|update|start|stop|resume`
- `openvas-cli report list|get`
- `openvas-cli config list`
- `openvas-cli config get`
- `openvas-cli scanner list`
- `openvas-cli credential list`
- `openvas-cli report-format list`
- `openvas-cli scan create`

Use `openvas-cli config list` to discover available scan configs. The CLI requests the full scan config set with `usage_type=scan` and disables default pagination before you choose one for `--scan-config`. Add `--details`, `--tasks`, or `--preferences` for richer output, or use `openvas-cli config get --name "Window-ClientOS" --details`.

Examples:

```bash
openvas-cli --json config list
openvas-cli --compact-json config get --name "Window-ClientOS" --details
```

## Query Filtering

- filter tasks by task status with a running state.
```bash
 openvas-cli --json task list --filter "status~running"
```
filtered output be like:

```json
{
  "tasks": [
    {
      "config_id": "90247c21-5118-4150-95fe-96763f29a3eb",
      "config_name": "Window-ClientOS",
      "id": "c343d933-f8ca-452a-8e6b-ee7127592c67",
      "last_report_id": "",
      "name": "Scan 192.168.20.10-254",
      "progress": "5",
      "scanner_id": "08b69003-5fc2-4037-a479-93b440211c73",
      "scanner_name": "OpenVAS Default",
      "status": "Running",
      "target_id": "6a732345-786e-4aae-9a95-3f99514669fb",
      "target_name": "Target 192.168.20.10-254"
    }
  ]
}

```

## Usage Notes

### Connection to a running GreenBone Openvas instance 

SSH is the default transport if `--transport` and `OPENVAS_TRANSPORT` are not set.

### Scan Tasks

`scan create` is the high level workflow.

It will find or create the target, find or create the task, update mismatched target or task bindings, and start the task unless it is already running.

For new targets, GMP 22.7 needs either `--port-list` or `--port-range`.

### Scan Reporting and Exports

`report get --format pdf` requires `--output` because the PDF is returned as base64 inside XML.
>>>>>>> d0380cd6bf06170132955c80a78320dfa161d8e9
