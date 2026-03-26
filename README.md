# openvas-cli

Python wrapper around native `gvm-cli`.

## Prerequisites

Before using `openvas-cli`, make sure these prerequisites are in place.

1. `python3 3.9+`
2. `gvm-tools 25.4.9`
3. `gvm-cli 25.4.9`
4. Access to a Greenbone or OpenVAS instance through one supported GMP transport
   `ssh`, `tls`, or `socket`
5. Required connection and login credentials
   SSH credentials if using `ssh`
   GMP credentials for Greenbone authentication
6. A Linux home path for secret config storage such as `~/.config/openvas-cli/openvas-cli.conf`

If missing, install these packages on Ubuntu or Debian:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv pipx
python3 -m pipx install gvm-tools
```

If `gvm-cli` is installed with `pipx`, make sure `~/.local/bin` is in `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Quick checks:

```bash
python3 --version
gvm-cli --version
bash ./openvas-cli/install.sh status
```

## Installation

Install the CLI into `~/.local/bin`:

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

Installer checks and behaviors:

1. Verifies source files exist
2. Verifies `python3` is available
3. Verifies `gvm-cli` is available in `PATH` or `~/.local/bin/gvm-cli`
4. Verifies the target install directory exists and is writable
5. Checks whether the target install directory is already visible in `PATH`
6. Automatically appends the install directory to your shell profile when needed
7. Recommends storing secrets in `~/.config/openvas-cli/openvas-cli.conf`
8. Warns if the workspace is on `/mnt/...`

## Onboarding

`onboard` walks through first time setup and saves a local config file.

Default path:

`~/.config/openvas-cli/openvas-cli.conf`

The file is written with permission `600` on Linux native filesystems.

Supported connection types shown during setup:

`[ssh|tls|socket]`

If `socket` is selected, `onboard` tries common gvmd socket paths first. If more than one path is found, it lists them and lets you choose before confirming the final socket path. The final socket path must exist before onboarding can continue.

At the end of onboarding, the CLI prints the saved config file location and suggested next commands.

Example:

```bash
openvas-cli onboard
openvas-cli doctor
```

## Quick Start

Shortest path from install to first scan:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv pipx
python3 -m pipx install gvm-tools
export PATH="$HOME/.local/bin:$PATH"

bash ./openvas-cli/install.sh install
openvas-cli onboard
openvas-cli doctor
openvas-cli credential list --filter "name~Windows"
openvas-cli config list --details
openvas-cli scan create --hosts 192.168.11.10-254 --credential Windows --scan-config "Full and fast" --port-range T:1-65535
```

## Subcommands

`openvas-cli onboard`
`openvas-cli doctor`
`openvas-cli system version`
`openvas-cli target list|get|create|update`
`openvas-cli task list|get|create|update|start|stop|resume`
`openvas-cli report list|get`
`openvas-cli config list`
`openvas-cli config get`
`openvas-cli scanner list`
`openvas-cli credential list`
`openvas-cli report-format list`
`openvas-cli scan create`

Use `openvas-cli config list` to discover available scan configs. The CLI requests the full scan config set with `usage_type=scan` and disables default pagination before you choose one for `--scan-config`. Add `--details`, `--tasks`, or `--preferences` for richer output, or use `openvas-cli config get --name "Full and fast" --details`.

Examples:

```bash
openvas-cli --json config list
openvas-cli --compact-json config get --name "Full and fast" --details
```

## Common global options

`--env-file PATH`
`--transport socket|tls|ssh` default is `ssh`
`--gmp-username USER`
`--gmp-password PASS`
`--config ~/.config/gvm-tools.conf`
`--socketpath /run/gvmd/gvmd.sock`
`--hostname HOST`
`--port PORT`
`--ssh-username USER`
`--ssh-password PASS`
`--certfile FILE`
`--keyfile FILE`
`--cafile FILE`
`--json` explicitly request JSON output
`--compact-json` emit single-line JSON output

## Usage Notes

SSH is the default transport if `--transport` and `OPENVAS_TRANSPORT` are not set.

`scan create` is the high level workflow.

It will find or create the target, find or create the task, update mismatched target or task bindings, and start the task unless it is already running.

For new targets, GMP 22.7 needs either `--port-list` or `--port-range`.

`report get --format pdf` requires `--output` because the PDF is returned as base64 inside XML.
