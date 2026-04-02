#!/usr/bin/env python3
import argparse
import ast
import getpass
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from base64 import b64decode
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

UUID_RE = re.compile(r"^[0-9a-fA-F-]{8,}$")
XML_REPORT_FORMAT_ID = "a994b278-1f62-11e1-96ac-406186ea4fc5"
PDF_REPORT_FORMAT_ID = "c402cc3e-b531-11e1-9163-406186ea4fc5"
DEFAULT_GVM_CLI = str(Path.home() / ".local/bin/gvm-cli")
DEFAULT_ENV_FILE = Path.home() / ".config" / "openvas-cli" / "openvas-cli.conf"
DEFAULT_SOCKET_PATH = "/run/gvmd/gvmd.sock"
SOCKET_CANDIDATES = [
    "/run/gvmd/gvmd.sock",
    "/run/gvm/gvmd.sock",
    "/run/openvas/openvasmd.sock",
    "/usr/share/gvm/gsad/web/gvmd.sock",
    "/usr/share/openvas/gsa/classic/openvasmd.sock",
]
JSON_INDENT = 2
JSON_SORT_KEYS = True
DEFAULT_SSH_PORT = "22"
SSH_TUNNEL_TIMEOUT = 10


class OpenvasCliError(Exception):
    pass


class GvmResponse:
    def __init__(self, raw_xml: str, root: ET.Element):
        self.raw_xml = raw_xml
        self.root = root
        self.status = root.attrib.get("status", "")
        self.status_text = root.attrib.get("status_text", "")

    def ok(self) -> bool:
        return self.status.startswith("2")

    def direct_children(self, tag: str) -> List[ET.Element]:
        return self.root.findall(f"./{tag}")

    def first(self, tag: str) -> Optional[ET.Element]:
        return self.root.find(f"./{tag}")



class JumpHostTunnel:
    """Manages an SSH local port-forward through a jump/bastion host.

    Uses SSH ControlMaster multiplexing so the tunnel is reused across
    subsequent invocations of openvas-cli.  The first run starts a
    background SSH process; later runs detect the control socket and
    skip the setup entirely.
    """

    def __init__(self, jump_host, jump_port, jump_username,
                 target_host, target_port, ssh_identity_file=None):
        self.jump_host = jump_host
        self.jump_port = jump_port or DEFAULT_SSH_PORT
        self.jump_username = jump_username
        self.target_host = target_host
        self.target_port = target_port or DEFAULT_SSH_PORT
        self.ssh_identity_file = ssh_identity_file
        self.local_port = self._derive_local_port()
        safe_host = re.sub(r"[^a-zA-Z0-9._-]", "_", self.jump_host)
        self.control_socket = Path(tempfile.gettempdir()) / f"openvas-cli-tunnel-{safe_host}-{self.jump_port}.sock"

    def _derive_local_port(self):
        """Derive a stable local port from the tunnel parameters so the same
        config always maps to the same port across invocations."""
        key = f"{self.jump_host}:{self.jump_port}:{self.target_host}:{self.target_port}"
        digest = int(hashlib.sha256(key.encode()).hexdigest(), 16)
        return 10000 + (digest % 55000)

    def _is_alive(self):
        """Check if an existing ControlMaster tunnel is still running."""
        if not self.control_socket.exists():
            return False
        jump_target = f"{self.jump_username}@{self.jump_host}" if self.jump_username else self.jump_host
        result = subprocess.run(
            ["ssh", "-S", str(self.control_socket), "-O", "check", jump_target],
            capture_output=True, text=True,
        )
        return result.returncode == 0

    def open(self):
        """Reuse an existing tunnel if alive, otherwise start a new one with ControlMaster."""
        if self._is_alive():
            return  # reuse existing tunnel

        # Clean up stale control socket if it exists
        self.control_socket.unlink(missing_ok=True)

        tunnel_cmd = [
            "ssh", "-f", "-N",
            "-M",
            "-S", str(self.control_socket),
            "-L", f"{self.local_port}:{self.target_host}:{self.target_port}",
            "-p", str(self.jump_port),
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ControlPersist=10m",
        ]
        if self.ssh_identity_file:
            tunnel_cmd.extend(["-i", self.ssh_identity_file])

        jump_target = f"{self.jump_username}@{self.jump_host}" if self.jump_username else self.jump_host
        tunnel_cmd.append(jump_target)

        # Use a temporary file for stderr and proc.wait() instead of subprocess.run()
        # to avoid a deadlock on older OpenSSH versions (e.g. 8.4): when ssh -f forks
        # a daemon, the daemon inherits the write end of the stderr pipe and keeps it
        # open, causing communicate() (used internally by subprocess.run) to block
        # indefinitely until the daemon exits. A plain file fd has no such blocking
        # semantics, and proc.wait() returns as soon as the foreground ssh process
        # exits without waiting for the daemon to close any inherited descriptor.
        # start_new_session=True isolates the SSH process in its own session so it is
        # not affected by the parent's process group or controlling terminal, which can
        # cause subtle failures on older systems.
        with tempfile.TemporaryFile() as stderr_tmp:
            proc = subprocess.Popen(
                tunnel_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_tmp,
                start_new_session=True,
            )
            returncode = proc.wait()
            if returncode != 0:
                stderr_tmp.seek(0)
                stderr = stderr_tmp.read().decode(errors="replace").strip()
                raise OpenvasCliError(f"SSH tunnel failed: {stderr}")

        self._wait_for_tunnel()

    def _wait_for_tunnel(self, timeout=SSH_TUNNEL_TIMEOUT):
        """Poll until the local forwarded port accepts connections."""
        import socket as _socket
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with _socket.create_connection(("127.0.0.1", self.local_port), timeout=1):
                    return
            except (ConnectionRefusedError, OSError):
                time.sleep(0.3)
        raise OpenvasCliError(f"SSH tunnel did not become ready within {timeout}s")

    def close(self):
        """No-op: tunnel persists via ControlPersist for reuse by subsequent commands.
        The tunnel auto-terminates after 10 minutes of inactivity."""
        pass

    def force_close(self):
        """Explicitly tear down the persistent tunnel via the control socket."""
        if self.control_socket.exists():
            jump_target = f"{self.jump_username}@{self.jump_host}" if self.jump_username else self.jump_host
            subprocess.run(
                ["ssh", "-S", str(self.control_socket), "-O", "exit", jump_target],
                capture_output=True,
            )

class GvmCliRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.gvm_cli_bin = self._resolve_gvm_cli_bin(args.gvm_cli_bin)
        self.env_file = self._resolve_env_file(args.env_file)
        self.file_env = self._load_env_file(self.env_file)
        self.env = dict(self.file_env)
        self.env.update(os.environ.copy())

    @staticmethod
    def _resolve_gvm_cli_bin(explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        if Path(DEFAULT_GVM_CLI).exists():
            return DEFAULT_GVM_CLI
        return "gvm-cli"

    def _resolve_env_file(self, explicit: Optional[str]) -> Path:
        if explicit:
            return Path(explicit).expanduser()
        if self.args.resource == "onboard" and getattr(self.args, "path", None):
            return Path(self.args.path).expanduser()
        env_path = os.environ.get("OPENVAS_ENV_FILE")
        if env_path:
            return Path(env_path).expanduser()
        return DEFAULT_ENV_FILE

    def _load_env_file(self, path: Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        data: Dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value and value[0] in {'"', "'"} and value[-1] == value[0]:
                value = ast.literal_eval(value)
            data[key] = value
        return data

    def env_value(self, key: str) -> str:
        return self.env.get(key, "")

    def _build_ssh_command(self, element: ET.Element, require_auth: bool = True) -> List[str]:
        host = self.args.hostname or self.env_value("OPENVAS_HOST")
        if not host:
            raise OpenvasCliError("SSH transport requires host, use onboard, env file, or --hostname")

        port = self.args.port or self.env_value("OPENVAS_PORT") or "22"
        ssh_username = self.args.ssh_username or self.env_value("OPENVAS_SSH_USERNAME")
        ssh_identity_file = self.args.ssh_identity_file or self.env_value("OPENVAS_SSH_IDENTITY_FILE")
        gmp_username = self.args.gmp_username or self.env_value("OPENVAS_GMP_USERNAME")
        gmp_password = self.args.gmp_password or self.env_value("OPENVAS_GMP_PASSWORD")
        socket_path = self.args.socketpath or self.env_value("OPENVAS_SOCKET_PATH") or DEFAULT_SOCKET_PATH
        remote_gvm_cli_bin = self.env_value("OPENVAS_REMOTE_GVM_CLI_BIN") or "gvm-cli"
        if not ssh_username:
            raise OpenvasCliError("SSH wrapper mode requires --ssh-username or OPENVAS_SSH_USERNAME")

        if require_auth and not self.args.config and not gmp_username:
            raise OpenvasCliError("missing GMP credentials, use onboard, env file, --gmp-username/--gmp-password, or --config")
        if self.args.config:
            raise OpenvasCliError("SSH transport wrapper mode does not support --config; use env file or explicit options")
        if ssh_identity_file and not Path(ssh_identity_file).expanduser().exists():
            raise OpenvasCliError(f"SSH identity file not found: {ssh_identity_file}")

        remote_command = [remote_gvm_cli_bin]
        if gmp_username:
            remote_command.extend(["--gmp-username", gmp_username])
        if gmp_password:
            remote_command.extend(["--gmp-password", gmp_password])
        remote_command.extend([
            "socket",
            "--socketpath",
            socket_path,
        ])
        remote_command.extend(["--xml", ET.tostring(element, encoding="unicode")])
        remote_command_text = " ".join(shlex.quote(part) for part in remote_command)

        ssh_command: List[str] = ["ssh", "-T", "-p", str(port)]
        if self.args.auto_accept_host:
            ssh_command.extend(["-o", "StrictHostKeyChecking=accept-new"])
        # When connecting through the jump host tunnel (host is 127.0.0.1),
        # skip host key checking for the local tunnel endpoint.
        if host == "127.0.0.1":
            ssh_command.extend([
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ])
        if ssh_identity_file:
            ssh_command.extend(["-i", ssh_identity_file])
        target = f"{ssh_username}@{host}" if ssh_username else str(host)
        ssh_command.extend([target, remote_command_text])

        return ssh_command

    def effective_transport(self) -> str:
        return self.args.transport or self.env_value("OPENVAS_TRANSPORT") or "ssh"

    def command_exists(self) -> bool:
        if Path(self.gvm_cli_bin).exists():
            return True
        return shutil.which(self.gvm_cli_bin) is not None

    def gvm_cli_version(self) -> str:
        completed = subprocess.run(
            [self.gvm_cli_bin, "--version"],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            raise OpenvasCliError((completed.stderr or completed.stdout or "unable to get gvm-cli version").strip())
        return (completed.stdout or "").strip()

    def build_base_command(self, require_auth: bool = True) -> List[str]:
        command = [self.gvm_cli_bin]
        if self.args.config:
            command.extend(["--config", self.args.config])
        if self.args.timeout is not None:
            command.extend(["--timeout", str(self.args.timeout)])

        gmp_username = self.args.gmp_username or self.env_value("OPENVAS_GMP_USERNAME")
        gmp_password = self.args.gmp_password or self.env_value("OPENVAS_GMP_PASSWORD")
        if gmp_username:
            command.extend(["--gmp-username", gmp_username])
        if gmp_password:
            command.extend(["--gmp-password", gmp_password])
        if require_auth and not self.args.config and not gmp_username:
            raise OpenvasCliError("missing GMP credentials, use onboard, env file, --gmp-username/--gmp-password, or --config")

        transport = self.effective_transport()
        command.append(transport)

        if transport == "socket":
            socket_path = self.args.socketpath or self.env_value("OPENVAS_SOCKET_PATH") or DEFAULT_SOCKET_PATH
            command.extend(["--socketpath", socket_path])
            return command

        if transport == "tls":
            host = self.args.hostname or self.env_value("OPENVAS_HOST")
            if not host:
                raise OpenvasCliError("TLS transport requires host, use onboard, env file, or --hostname")
            port = self.args.port or self.env_value("OPENVAS_PORT") or "9390"
            command.extend(["--hostname", host, "--port", str(port)])
            certfile = self.args.certfile or self.env_value("OPENVAS_TLS_CERTFILE")
            keyfile = self.args.keyfile or self.env_value("OPENVAS_TLS_KEYFILE")
            cafile = self.args.cafile or self.env_value("OPENVAS_TLS_CAFILE")
            if certfile:
                command.extend(["--certfile", certfile])
            if keyfile:
                command.extend(["--keyfile", keyfile])
            if cafile:
                command.extend(["--cafile", cafile])
            if not require_auth and self.args.no_credentials:
                command.append("--no-credentials")
            return command

        if transport == "ssh":
            host = self.args.hostname or self.env_value("OPENVAS_HOST")
            if not host:
                raise OpenvasCliError("SSH transport requires host, use onboard, env file, or --hostname")
            port = self.args.port or self.env_value("OPENVAS_PORT") or "22"
            command.extend(["--hostname", host, "--port", str(port)])
            ssh_username = self.args.ssh_username or self.env_value("OPENVAS_SSH_USERNAME")
            if ssh_username:
                command.extend(["--ssh-username", ssh_username])
            if self.args.auto_accept_host:
                command.append("--auto-accept-host")
            return command

        raise OpenvasCliError(f"unsupported transport: {transport}")

    def invoke_xml(self, element: ET.Element, require_auth: bool = True) -> GvmResponse:
        transport = self.effective_transport()
        if transport == "ssh":
            command = self._build_ssh_command(element, require_auth=require_auth)
        else:
            command = self.build_base_command(require_auth=require_auth)
            xml_text = ET.tostring(element, encoding="unicode")
            command.extend(["--xml", xml_text])

        if self.args.debug:
            gmp_password = self.args.gmp_password or self.env_value("OPENVAS_GMP_PASSWORD")
            masked = []
            for part in command:
                value = part
                if gmp_password:
                    value = value.replace(gmp_password, "***")
                masked.append(value)
            print(" ".join(masked), file=sys.stderr)

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or f"gvm-cli exited with {completed.returncode}").strip()
            raise OpenvasCliError(message)

        stdout = (completed.stdout or "").strip()
        if not stdout:
            raise OpenvasCliError("gvm-cli returned empty output")

        try:
            root = ET.fromstring(stdout)
        except ET.ParseError as exc:
            raise OpenvasCliError(f"XML parse error: {exc}") from exc

        response = GvmResponse(stdout, root)
        if not response.ok():
            raise OpenvasCliError(response.status_text or response.raw_xml)
        return response


class OnboardWriter:
    def __init__(self, args: argparse.Namespace, runner: GvmCliRunner):
        self.args = args
        self.runner = runner
        self.target_path = Path(args.path or runner.env_file).expanduser()
        self.current = dict(runner.file_env)
        self.current.update({k: v for k, v in os.environ.items() if k.startswith("OPENVAS_")})

    def run(self) -> None:
        if self.target_path.exists() and not self.args.force:
            raise OpenvasCliError(f"config file exists: {self.target_path}. Use --force to overwrite.")

        values = self.collect_values()
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# openvas-cli onboarding output",
            f"OPENVAS_TRANSPORT={json.dumps(values['OPENVAS_TRANSPORT'])}",
        ]
        for key in [
            "OPENVAS_HOST",
            "OPENVAS_PORT",
            "OPENVAS_SOCKET_PATH",
            "OPENVAS_SSH_USERNAME",
            "OPENVAS_SSH_IDENTITY_FILE",
            "OPENVAS_REMOTE_GVM_CLI_BIN",
            "OPENVAS_GMP_USERNAME",
            "OPENVAS_GMP_PASSWORD",
            "OPENVAS_TLS_CERTFILE",
            "OPENVAS_TLS_KEYFILE",
            "OPENVAS_TLS_CAFILE",
            "OPENVAS_JUMP_HOST",
            "OPENVAS_JUMP_PORT",
            "OPENVAS_JUMP_SSH_USERNAME",
        ]:
            value = values.get(key, "")
            if value:
                lines.append(f"{key}={json.dumps(value)}")
        self.target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(self.target_path, 0o600)

        print(f"Saved config: {self.target_path}")
        print(f"Config file location: {self.target_path}")
        print("Permissions: 600")
        print("Next commands:")
        print(f"  openvas-cli --env-file {self.target_path} doctor")
        print(f"  openvas-cli --env-file {self.target_path} system version")

        if self.args.test:
            self.run_doctor(values)

    def run_doctor(self, values: Dict[str, str]) -> None:
        env = os.environ.copy()
        env.update(values)
        command = [sys.executable, str(Path(__file__).resolve()), "--env-file", str(self.target_path), "doctor"]
        completed = subprocess.run(command, text=True, capture_output=True, env=env)
        print("Doctor output:")
        output = (completed.stdout or completed.stderr or "").rstrip()
        if output:
            print(output)

    def add_ssh_host_to_known_hosts(self, host: str, port: str) -> None:
        if shutil.which("ssh-keyscan") is None:
            print("ssh-keyscan not found, skipping known_hosts update.")
            return
        ssh_dir = Path.home() / ".ssh"
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        known_hosts = ssh_dir / "known_hosts"
        host_key_name = host if str(port) == "22" else f"[{host}]:{port}"
        check = subprocess.run(["ssh-keygen", "-F", host_key_name], capture_output=True, text=True)
        if check.returncode == 0 and check.stdout.strip():
            print(f"SSH host already present in known_hosts: {host_key_name}")
            return
        scan = subprocess.run(["ssh-keyscan", "-H", "-p", str(port), host], capture_output=True, text=True)
        if scan.returncode != 0 or not scan.stdout.strip():
            print(f"Failed to fetch SSH host key for {host_key_name}. Add it manually if needed.")
            return
        with known_hosts.open("a", encoding="utf-8") as handle:
            handle.write(scan.stdout)
        if known_hosts.exists():
            os.chmod(known_hosts, 0o600)
        print(f"Added SSH host key to {known_hosts}: {host_key_name}")

    def ensure_ssh_keypair(self, identity_path: str) -> Path:
        target = Path(identity_path).expanduser()
        ssh_dir = target.parent
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(ssh_dir, 0o700)
        pub_path = Path(str(target) + ".pub")
        if target.exists() and pub_path.exists():
            return target
        if shutil.which("ssh-keygen") is None:
            raise OpenvasCliError("ssh-keygen not found; unable to generate SSH identity")
        completed = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(target), "-N", "", "-C", "openvas-cli"],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise OpenvasCliError((completed.stderr or completed.stdout or "ssh-keygen failed").strip())
        return target

    def install_ssh_public_key(self, host: str, port: str, ssh_username: str, ssh_password: str, identity_path: Path) -> None:
        pub_path = Path(str(identity_path) + ".pub")
        if not pub_path.exists():
            raise OpenvasCliError(f"SSH public key not found: {pub_path}")
        if not ssh_password:
            raise OpenvasCliError("SSH password required during onboarding to install the generated SSH public key")
        if shutil.which("sshpass") is None:
            raise OpenvasCliError("sshpass is required during onboarding to install the generated SSH public key")

        public_key = pub_path.read_text(encoding="utf-8").strip()
        remote_script = (
            "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
            "grep -qxF {key} ~/.ssh/authorized_keys || printf '%s\\n' {key} >> ~/.ssh/authorized_keys"
        ).format(key=shlex.quote(public_key))

        command = [
            "sshpass", "-p", ssh_password,
            "ssh", "-T", "-p", str(port),
            "-o", "StrictHostKeyChecking=accept-new",
            f"{ssh_username}@{host}",
            remote_script,
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise OpenvasCliError((completed.stderr or completed.stdout or "failed to install SSH public key").strip())

    def bootstrap_ssh_identity(self, host: str, port: str, ssh_username: str, identity_path: str,
                               jump_host: str = "", jump_port: str = "", jump_ssh_username: str = "") -> str:
        identity = self.ensure_ssh_keypair(identity_path)
        if jump_host:
            self.add_ssh_host_to_known_hosts(jump_host, jump_port or DEFAULT_SSH_PORT)
        self.add_ssh_host_to_known_hosts(host, port)
        password = self.prompt_secret("SSH password (used once to install generated public key)", required=True)
        if jump_host:
            self.install_ssh_public_key(jump_host, jump_port or DEFAULT_SSH_PORT, jump_ssh_username, password, identity)
            print(f"SSH key installed for {jump_ssh_username}@{jump_host}.")
            tunnel = JumpHostTunnel(
                jump_host=jump_host,
                jump_port=jump_port or DEFAULT_SSH_PORT,
                jump_username=jump_ssh_username,
                target_host=host,
                target_port=port or DEFAULT_SSH_PORT,
                ssh_identity_file=str(identity),
            )
            tunnel.open()
            try:
                self.install_ssh_public_key("127.0.0.1", str(tunnel.local_port), ssh_username, password, identity)
            finally:
                tunnel.close()
            print(f"SSH key installed for {ssh_username}@{host} through jump host. Future commands will use {identity}.")
        else:
            self.install_ssh_public_key(host, port, ssh_username, password, identity)
            print(f"SSH key installed for {ssh_username}@{host}. Future commands will use {identity}.")
        return str(identity)

    def detect_socket_paths(self) -> List[str]:
        found: List[str] = []
        for candidate in SOCKET_CANDIDATES:
            if Path(candidate).exists():
                found.append(candidate)
        return found

    def select_socket_path(self, candidates: List[str], default: str) -> str:
        if not candidates:
            return default
        if len(candidates) == 1:
            print(f"Detected socket path: {candidates[0]}")
            return candidates[0]
        print("Detected multiple socket paths:")
        for index, candidate in enumerate(candidates, start=1):
            print(f"  {index}. {candidate}")
        while True:
            choice = input(f"Select socket path [1-{len(candidates)}] or press Enter for {default}: ").strip()
            if not choice:
                return default
            if choice.isdigit():
                selected = int(choice)
                if 1 <= selected <= len(candidates):
                    return candidates[selected - 1]
            print("Enter a valid number or press Enter to keep the default.")

    def collect_values(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        print("Supported connection types [ssh|tls|socket]")
        default_transport = self.current.get("OPENVAS_TRANSPORT", "ssh") or "ssh"
        transport = self.prompt("Connection type", default=default_transport, allowed={"ssh", "tls", "socket"})
        values["OPENVAS_TRANSPORT"] = transport

        if transport == "socket":
            detected_sockets = self.detect_socket_paths()
            default_socket = self.current.get("OPENVAS_SOCKET_PATH") or (detected_sockets[0] if detected_sockets else DEFAULT_SOCKET_PATH)
            if detected_sockets:
                default_socket = self.select_socket_path(detected_sockets, default_socket)
            else:
                print("No known gvmd socket found. Enter socket path manually.")
            values["OPENVAS_SOCKET_PATH"] = self.prompt_existing_path("Socket path", default=default_socket, required=True)
        else:
            default_host = self.current.get("OPENVAS_HOST", "")
            values["OPENVAS_HOST"] = self.prompt("Host", default=default_host, required=True)
            default_port = self.current.get("OPENVAS_PORT", "22" if transport == "ssh" else "9390")
            values["OPENVAS_PORT"] = self.prompt("Port", default=default_port, required=True)

        if transport == "ssh":
            values["OPENVAS_SSH_USERNAME"] = self.prompt("SSH username", default=self.current.get("OPENVAS_SSH_USERNAME", "gmp"), required=True)
            use_jump = self.prompt("Use a jump/bastion host?", default="no", allowed={"yes", "no"})
            if use_jump == "yes":
                values["OPENVAS_JUMP_HOST"] = self.prompt("Jump host", default=self.current.get("OPENVAS_JUMP_HOST", ""), required=True)
                values["OPENVAS_JUMP_PORT"] = self.prompt("Jump host SSH port", default=self.current.get("OPENVAS_JUMP_PORT", DEFAULT_SSH_PORT))
                values["OPENVAS_JUMP_SSH_USERNAME"] = self.prompt("Jump host SSH username", default=self.current.get("OPENVAS_JUMP_SSH_USERNAME", ""), required=True)
            default_identity = self.current.get("OPENVAS_SSH_IDENTITY_FILE", str(Path.home() / ".ssh" / "openvas_cli_ed25519"))
            values["OPENVAS_SSH_IDENTITY_FILE"] = self.bootstrap_ssh_identity(
                values["OPENVAS_HOST"],
                values["OPENVAS_PORT"],
                values["OPENVAS_SSH_USERNAME"],
                default_identity,
                jump_host=values.get("OPENVAS_JUMP_HOST", ""),
                jump_port=values.get("OPENVAS_JUMP_PORT", ""),
                jump_ssh_username=values.get("OPENVAS_JUMP_SSH_USERNAME", ""),
            )
            values["OPENVAS_REMOTE_GVM_CLI_BIN"] = self.prompt("Remote gvm-cli path", default=self.current.get("OPENVAS_REMOTE_GVM_CLI_BIN", "gvm-cli"), required=True)

        if transport == "tls":
            values["OPENVAS_TLS_CERTFILE"] = self.prompt("TLS certfile", default=self.current.get("OPENVAS_TLS_CERTFILE", ""), required=False)
            values["OPENVAS_TLS_KEYFILE"] = self.prompt("TLS keyfile", default=self.current.get("OPENVAS_TLS_KEYFILE", ""), required=False)
            values["OPENVAS_TLS_CAFILE"] = self.prompt("TLS cafile", default=self.current.get("OPENVAS_TLS_CAFILE", ""), required=False)

        values["OPENVAS_GMP_USERNAME"] = self.prompt("GMP username", default=self.current.get("OPENVAS_GMP_USERNAME", ""), required=True)
        values["OPENVAS_GMP_PASSWORD"] = self.prompt_secret("GMP password", default=self.current.get("OPENVAS_GMP_PASSWORD", ""), required=True)
        return values

    def prompt_existing_path(self, label: str, default: str = "", required: bool = False) -> str:
        while True:
            value = self.prompt(label, default=default, required=required)
            if not value and not required:
                return value
            if Path(value).exists():
                return value
            print(f"{label} does not exist: {value}")

    def prompt(self, label: str, default: str = "", required: bool = False, allowed: Optional[set] = None) -> str:
        suffix = f" [{default}]" if default else ""
        while True:
            value = input(f"{label}{suffix}: ").strip()
            if not value:
                value = default
            if not value and required:
                print(f"{label} is required.")
                continue
            if allowed and value and value not in allowed:
                print(f"Allowed values: {', '.join(sorted(allowed))}")
                continue
            return value

    def prompt_secret(self, label: str, default: str = "", required: bool = False) -> str:
        suffix = " [saved value available]" if default else ""
        while True:
            value = getpass.getpass(f"{label}{suffix}: ")
            if not value:
                value = default
            if not value and required:
                print(f"{label} is required.")
                continue
            return value


def _json_print(payload: Dict) -> None:
    print(json.dumps(payload, indent=JSON_INDENT, sort_keys=JSON_SORT_KEYS))


def _uuid_like(value: Optional[str]) -> bool:
    return bool(value and UUID_RE.match(value))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openvas-cli")
    _add_global_options(parser)
    subparsers = parser.add_subparsers(dest="resource")

    onboard = subparsers.add_parser("onboard")
    onboard.add_argument("--path")
    onboard.add_argument("--force", action="store_true")
    onboard.add_argument("--test", action="store_true")

    subparsers.add_parser("doctor")

    system = subparsers.add_parser("system")
    system_subparsers = system.add_subparsers(dest="action")
    system_subparsers.add_parser("version")

    target = subparsers.add_parser("target")
    target_subparsers = target.add_subparsers(dest="action")
    target_list = target_subparsers.add_parser("list")
    target_list.add_argument("--filter")
    target_list.add_argument("--details", action="store_true")
    target_list.add_argument("--tasks", action="store_true")

    target_get = target_subparsers.add_parser("get")
    _add_lookup_arguments(target_get)

    target_create = target_subparsers.add_parser("create")
    target_create.add_argument("--name", required=True)
    target_create.add_argument("--hosts", required=True)
    target_create.add_argument("--exclude-hosts")
    target_create.add_argument("--credential")
    target_create.add_argument("--port-list")
    target_create.add_argument("--port-range")

    target_update = target_subparsers.add_parser("update")
    _add_lookup_arguments(target_update)
    target_update.add_argument("--set-name")
    target_update.add_argument("--hosts")
    target_update.add_argument("--exclude-hosts")
    target_update.add_argument("--credential")
    target_update.add_argument("--port-list")

    task = subparsers.add_parser("task")
    task_subparsers = task.add_subparsers(dest="action")
    task_list = task_subparsers.add_parser("list")
    task_list.add_argument("--filter")
    task_list.add_argument("--details", action="store_true")

    task_get = task_subparsers.add_parser("get")
    _add_lookup_arguments(task_get)

    task_create = task_subparsers.add_parser("create")
    task_create.add_argument("--name", required=True)
    task_create.add_argument("--target", required=True)
    task_create.add_argument("--scan-config", required=True, dest="scan_config")
    task_create.add_argument("--scanner")

    task_update = task_subparsers.add_parser("update")
    _add_lookup_arguments(task_update)
    task_update.add_argument("--set-name")
    task_update.add_argument("--target")
    task_update.add_argument("--scan-config", dest="scan_config")
    task_update.add_argument("--scanner")

    for action in ("start", "stop", "resume"):
        task_action = task_subparsers.add_parser(action)
        _add_lookup_arguments(task_action)

    report = subparsers.add_parser("report")
    report_subparsers = report.add_subparsers(dest="action")
    report_list = report_subparsers.add_parser("list")
    report_list.add_argument("--filter")

    report_get = report_subparsers.add_parser("get")
    report_get.add_argument("--id", required=True)
    report_get.add_argument("--format", default="xml")
    report_get.add_argument("--output")

    config = subparsers.add_parser("config")
    config_subparsers = config.add_subparsers(dest="action")
    config_list = config_subparsers.add_parser("list")
    config_list.add_argument("--filter")
    config_list.add_argument("--details", action="store_true")
    config_list.add_argument("--tasks", action="store_true")
    config_list.add_argument("--preferences", action="store_true")
    config_get = config_subparsers.add_parser("get")
    _add_lookup_arguments(config_get)
    config_get.add_argument("--details", action="store_true")
    config_get.add_argument("--tasks", action="store_true")
    config_get.add_argument("--preferences", action="store_true")

    scanner = subparsers.add_parser("scanner")
    scanner_subparsers = scanner.add_subparsers(dest="action")
    scanner_list = scanner_subparsers.add_parser("list")
    scanner_list.add_argument("--filter")

    credential = subparsers.add_parser("credential")
    credential_subparsers = credential.add_subparsers(dest="action")
    credential_list = credential_subparsers.add_parser("list")
    credential_list.add_argument("--filter")

    credential_get = credential_subparsers.add_parser("get")
    _add_lookup_arguments(credential_get)
    credential_get.add_argument("--details", action="store_true")

    credential_create = credential_subparsers.add_parser("create")
    credential_create.add_argument("--name", required=True)
    credential_create.add_argument("--type", required=True, choices=["up", "usk", "snmp"], help="Credential type: up=username+password, usk=username+ssh-key, snmp=snmp")
    credential_create.add_argument("--comment")
    credential_create.add_argument("--username")
    credential_create.add_argument("--password")
    credential_create.add_argument("--private-key", dest="private_key")
    credential_create.add_argument("--passphrase")
    credential_create.add_argument("--community")
    credential_create.add_argument("--snmp-username", dest="snmp_username")
    credential_create.add_argument("--snmp-auth-password", dest="snmp_auth_password")
    credential_create.add_argument("--snmp-auth-protocol", dest="snmp_auth_protocol", choices=["md5", "sha1"])
    credential_create.add_argument("--snmp-priv-password", dest="snmp_priv_password")
    credential_create.add_argument("--snmp-priv-protocol", dest="snmp_priv_protocol", choices=["aes", "des", "none"])

    credential_update = credential_subparsers.add_parser("update")
    _add_lookup_arguments(credential_update)
    credential_update.add_argument("--set-name", dest="set_name")
    credential_update.add_argument("--comment")
    credential_update.add_argument("--username")
    credential_update.add_argument("--password")
    credential_update.add_argument("--private-key", dest="private_key")
    credential_update.add_argument("--passphrase")
    credential_update.add_argument("--community")
    credential_update.add_argument("--snmp-username", dest="snmp_username")
    credential_update.add_argument("--snmp-auth-password", dest="snmp_auth_password")
    credential_update.add_argument("--snmp-auth-protocol", dest="snmp_auth_protocol", choices=["md5", "sha1"])
    credential_update.add_argument("--snmp-priv-password", dest="snmp_priv_password")
    credential_update.add_argument("--snmp-priv-protocol", dest="snmp_priv_protocol", choices=["aes", "des", "none"])

    credential_delete = credential_subparsers.add_parser("delete")
    _add_lookup_arguments(credential_delete)
    credential_delete.add_argument("--force", action="store_true")

    report_format = subparsers.add_parser("report-format")
    report_format_subparsers = report_format.add_subparsers(dest="action")
    report_format_list = report_format_subparsers.add_parser("list")
    report_format_list.add_argument("--filter")

    scan = subparsers.add_parser("scan")
    scan_subparsers = scan.add_subparsers(dest="action")
    scan_create = scan_subparsers.add_parser("create")
    scan_create.add_argument("--hosts", required=True)
    scan_create.add_argument("--credential")
    scan_create.add_argument("--port-list")
    scan_create.add_argument("--port-range")
    scan_create.add_argument("--target-name")
    scan_create.add_argument("--task-name")
    scan_create.add_argument("--scan-config", default="Full and Fast", dest="scan_config")
    scan_create.add_argument("--scanner", default="OpenVAS Default")

    return parser


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--gvm-cli-bin")
    parser.add_argument("--config")
    parser.add_argument("--env-file")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--gmp-username")
    parser.add_argument("--gmp-password")
    parser.add_argument("--transport", choices=["socket", "tls", "ssh"])
    parser.add_argument("--socketpath")
    parser.add_argument("--hostname")
    parser.add_argument("--port", type=int)
    parser.add_argument("--ssh-username")
    parser.add_argument("--ssh-identity-file")
    parser.add_argument("--auto-accept-host", action="store_true")
    parser.add_argument("--certfile")
    parser.add_argument("--keyfile")
    parser.add_argument("--cafile")
    parser.add_argument("--no-credentials", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json", action="store_true", help="Explicitly request JSON output")
    parser.add_argument("--compact-json", action="store_true", help="Emit single-line JSON output")


def _add_lookup_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id")
    parser.add_argument("--name")


def _require_lookup(args: argparse.Namespace) -> Tuple[str, str]:
    if args.id:
        return "id", args.id
    if args.name:
        return "name", args.name
    raise OpenvasCliError("use --id or --name")


def _make_simple_request(command_name: str, **attributes: str) -> ET.Element:
    element = ET.Element(command_name)
    for key, value in attributes.items():
        if value is not None and value != "":
            element.set(key, str(value))
    return element


def _resolve_resource_id(runner: GvmCliRunner, command_name: str, node_name: str, value: str) -> str:
    if _uuid_like(value):
        return value
    request = _make_simple_request(command_name, filter=f"name={value}")
    response = runner.invoke_xml(request)
    for node in response.direct_children(node_name):
        if node.findtext("name") == value:
            resource_id = node.attrib.get("id")
            if resource_id:
                return resource_id
    raise OpenvasCliError(f"{node_name} not found: {value}")


def _lookup_direct_child(
    runner: GvmCliRunner,
    command_name: str,
    node_name: str,
    value_type: str,
    value: str,
    details: bool = True,
    tasks: bool = False,
) -> ET.Element:
    attributes: Dict[str, str] = {}
    if value_type == "id":
        attributes[f"{node_name}_id"] = value
    else:
        attributes["filter"] = f"name={value}"
    if details:
        attributes["details"] = "1"
    if tasks:
        attributes["tasks"] = "1"
    response = runner.invoke_xml(_make_simple_request(command_name, **attributes))
    candidates = response.direct_children(node_name)
    if value_type == "id" and candidates:
        return candidates[0]
    for node in candidates:
        if node.findtext("name") == value:
            return node
    raise OpenvasCliError(f"{node_name} not found: {value}")


def _child_attr(node: ET.Element, child_name: str, attribute_name: str) -> str:
    child = node.find(child_name)
    if child is None:
        return ""
    return child.attrib.get(attribute_name, "")


def _child_text(node: ET.Element, child_name: str, nested_name: str) -> str:
    child = node.find(child_name)
    if child is None:
        return ""
    return child.findtext(nested_name, default="")


def _deep_attr(node: ET.Element, path: Iterable[str], attribute_name: str) -> str:
    current = node
    for segment in path:
        current = current.find(segment)
        if current is None:
            return ""
    return current.attrib.get(attribute_name, "")


def _target_json(node: ET.Element) -> Dict:
    return {
        "id": node.attrib.get("id", ""),
        "name": node.findtext("name", default=""),
        "hosts": node.findtext("hosts", default=""),
        "exclude_hosts": node.findtext("exclude_hosts", default=""),
        "port_list_id": _child_attr(node, "port_list", "id"),
        "port_list_name": _child_text(node, "port_list", "name"),
        "smb_credential_id": _child_attr(node, "smb_credential", "id"),
        "smb_credential_name": _child_text(node, "smb_credential", "name"),
        "in_use": node.findtext("in_use", default=""),
    }


def _task_json(node: ET.Element) -> Dict:
    return {
        "id": node.attrib.get("id", ""),
        "name": node.findtext("name", default=""),
        "status": node.findtext("status", default=""),
        "progress": node.findtext("progress", default=""),
        "target_id": _child_attr(node, "target", "id"),
        "target_name": _child_text(node, "target", "name"),
        "config_id": _child_attr(node, "config", "id"),
        "config_name": _child_text(node, "config", "name"),
        "scanner_id": _child_attr(node, "scanner", "id"),
        "scanner_name": _child_text(node, "scanner", "name"),
        "last_report_id": _deep_attr(node, ["last_report", "report"], "id"),
    }


def _report_json(node: ET.Element) -> Dict:
    task = node.find("task")
    return {
        "id": node.attrib.get("id", ""),
        "format_id": node.attrib.get("format_id", ""),
        "task_name": task.findtext("name", default="") if task is not None else "",
        "scan_run_status": node.findtext("scan_run_status", default=""),
        "timestamp": node.findtext("timestamp", default=""),
    }


def _generic_named_json(node: ET.Element) -> Dict:
    return {
        "id": node.attrib.get("id", ""),
        "name": node.findtext("name", default=""),
    }


def _credential_json(node: ET.Element, include_details: bool = False) -> Dict:
    cred_type = node.findtext("type", default="")
    payload = {
        "id": node.attrib.get("id", ""),
        "name": node.findtext("name", default=""),
        "type": cred_type,
        "comment": node.findtext("comment", default=""),
        "creation_time": node.findtext("creation_time", default=""),
        "modification_time": node.findtext("modification_time", default=""),
        "in_use": _text_to_bool(node.findtext("in_use", default="")),
        "writable": _text_to_bool(node.findtext("writable", default="")),
        "owner": _child_text(node, "owner", "name"),
    }
    if include_details:
        login_elem = node.find("login")
        if login_elem is not None:
            payload["login"] = login_elem.text or ""
        if cred_type in ("up", "usk"):
            payload["allow_insecure"] = _text_to_bool(node.findtext("allow_insecure", default=""))
        elif cred_type == "snmp":
            payload["community"] = node.findtext("community", default="")
            payload["auth_algorithm"] = node.findtext("auth_algorithm", default="")
            privacy_elem = node.find("privacy")
            if privacy_elem is not None:
                payload["privacy_algorithm"] = privacy_elem.findtext("algorithm", default="")
        if cred_type == "usk":
            public_key_elem = node.find("public_key")
            if public_key_elem is not None:
                payload["public_key"] = public_key_elem.text or ""
    return payload


def _config_json(node: ET.Element, include_details: bool = False, include_tasks: bool = False, include_preferences: bool = False) -> Dict:
    payload = {
        "id": node.attrib.get("id", ""),
        "name": node.findtext("name", default=""),
        "comment": node.findtext("comment", default=""),
        "owner": _child_text(node, "owner", "name"),
        "creation_time": node.findtext("creation_time", default=""),
        "modification_time": node.findtext("modification_time", default=""),
        "family_count": _text_to_int(node.findtext("family_count", default="")),
        "family_count_growing": _text_to_bool(_child_text(node, "family_count", "growing")),
        "nvt_count": _text_to_int(node.findtext("nvt_count", default="")),
        "nvt_count_growing": _text_to_bool(_child_text(node, "nvt_count", "growing")),
        "type": _text_to_int(node.findtext("type", default="")),
        "usage_type": node.findtext("usage_type", default=""),
        "predefined": _text_to_bool(node.findtext("predefined", default="")),
        "in_use": _text_to_bool(node.findtext("in_use", default="")),
        "writable": _text_to_bool(node.findtext("writable", default="")),
        "deprecated": _text_to_bool(node.findtext("deprecated", default="")),
    }
    if include_tasks:
        payload["tasks"] = [
            {
                "id": task.attrib.get("id", ""),
                "name": task.findtext("name", default=""),
            }
            for task in node.findall("./tasks/task")
        ]
    if include_preferences or include_details:
        payload["preferences"] = [
            {
                "id": pref.findtext("id", default=""),
                "name": pref.findtext("name", default=""),
                "type": pref.findtext("type", default=""),
                "value": pref.findtext("value", default=""),
                "default": pref.findtext("default", default=""),
                "nvt_oid": _child_attr(pref, "nvt", "oid"),
                "nvt_name": _child_text(pref, "nvt", "name"),
            }
            for pref in node.findall("./preferences/preference")
        ]
    if include_details:
        payload["families"] = [
            {
                "name": family.findtext("name", default=""),
                "nvt_count": _text_to_int(family.findtext("nvt_count", default="")),
                "max_nvt_count": _text_to_int(family.findtext("max_nvt_count", default="")),
                "growing": family.findtext("growing", default=""),
            }
            for family in node.findall("./families/family")
        ]
    return payload


def _text_to_int(value: str):
    value = (value or "").strip()
    return int(value) if value.isdigit() else value


def _text_to_bool(value: str):
    value = (value or "").strip()
    if value == "1":
        return True
    if value == "0":
        return False
    return value


def _create_target(args: argparse.Namespace, runner: GvmCliRunner) -> Dict:
    if not args.port_list and not args.port_range:
        raise OpenvasCliError("target create requires --port-list or --port-range")
    request = ET.Element("create_target")
    ET.SubElement(request, "name").text = args.name
    ET.SubElement(request, "hosts").text = args.hosts
    if args.exclude_hosts:
        ET.SubElement(request, "exclude_hosts").text = args.exclude_hosts
    if args.credential:
        credential_id = _resolve_resource_id(runner, "get_credentials", "credential", args.credential)
        ET.SubElement(request, "smb_credential", id=credential_id)
    if args.port_list:
        port_list_id = _resolve_resource_id(runner, "get_port_lists", "port_list", args.port_list)
        ET.SubElement(request, "port_list", id=port_list_id)
    if args.port_range:
        ET.SubElement(request, "port_range").text = args.port_range
    response = runner.invoke_xml(request)
    return {
        "id": response.root.attrib.get("id", ""),
        "status": response.status,
        "status_text": response.status_text,
    }


def _create_task(args: argparse.Namespace, runner: GvmCliRunner) -> Dict:
    target_id = _resolve_resource_id(runner, "get_targets", "target", args.target)
    config_id = _resolve_resource_id(runner, "get_configs", "config", args.scan_config)
    request = ET.Element("create_task")
    ET.SubElement(request, "name").text = args.name
    ET.SubElement(request, "config", id=config_id)
    ET.SubElement(request, "target", id=target_id)
    if args.scanner:
        scanner_id = _resolve_resource_id(runner, "get_scanners", "scanner", args.scanner)
        ET.SubElement(request, "scanner", id=scanner_id)
    response = runner.invoke_xml(request)
    return {
        "id": response.root.attrib.get("id", ""),
        "status": response.status,
        "status_text": response.status_text,
    }


def command_onboard(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    OnboardWriter(args, runner).run()


def command_doctor(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    checks = []
    checks.append({
        "name": "gvm-cli",
        "ok": runner.command_exists(),
        "detail": runner.gvm_cli_bin if runner.command_exists() else f"not found: {runner.gvm_cli_bin}",
    })
    checks.append({
        "name": "transport",
        "ok": True,
        "detail": runner.effective_transport(),
    })
    checks.append({
        "name": "env_file",
        "ok": True,
        "detail": str(runner.env_file) if runner.env_file.exists() else f"not found: {runner.env_file}",
    })
    jump_host = runner.env_value("OPENVAS_JUMP_HOST")
    if jump_host:
        jump_port = runner.env_value("OPENVAS_JUMP_PORT") or DEFAULT_SSH_PORT
        try:
            scan = subprocess.run(
                ["ssh-keyscan", "-H", "-p", jump_port, jump_host],
                capture_output=True, text=True, timeout=SSH_TUNNEL_TIMEOUT,
            )
            checks.append({
                "name": "jump_host_reachable",
                "ok": scan.returncode == 0 and bool(scan.stdout.strip()),
                "detail": f"{jump_host}:{jump_port}",
            })
        except subprocess.TimeoutExpired:
            checks.append({
                "name": "jump_host_reachable",
                "ok": False,
                "detail": f"timeout: {jump_host}:{jump_port}",
            })
    if checks[0]["ok"]:
        try:
            checks.append({"name": "gvm-cli-version", "ok": True, "detail": runner.gvm_cli_version()})
        except OpenvasCliError as exc:
            checks.append({"name": "gvm-cli-version", "ok": False, "detail": str(exc)})
    if checks[0]["ok"]:
        try:
            response = runner.invoke_xml(_make_simple_request("get_version"), require_auth=False)
            checks.append({"name": "get_version", "ok": True, "detail": response.root.findtext("version", default="unknown")})
        except OpenvasCliError as exc:
            checks.append({"name": "get_version", "ok": False, "detail": str(exc)})

    for check in checks:
        prefix = "OK" if check["ok"] else "FAIL"
        print(f"{prefix} {check['name']}: {check['detail']}")

    if any(not item["ok"] for item in checks):
        raise OpenvasCliError("doctor found issues")


def command_system_version(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    response = runner.invoke_xml(_make_simple_request("get_version"), require_auth=False)
    _json_print({
        "status": response.status,
        "status_text": response.status_text,
        "version": response.root.findtext("version", default=""),
    })


def command_target_list(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    request = _make_simple_request("get_targets")
    if args.filter:
        request.set("filter", args.filter)
    if args.details:
        request.set("details", "1")
    if args.tasks:
        request.set("tasks", "1")
    response = runner.invoke_xml(request)
    _json_print({"targets": [_target_json(node) for node in response.direct_children("target")]})


def command_target_get(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_targets", "target", value_type, value, details=True, tasks=True)
    _json_print({"target": _target_json(node)})


def command_target_create(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    _json_print(_create_target(args, runner))


def command_target_update(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_targets", "target", value_type, value, details=True, tasks=True)
    target_id = node.attrib.get("id", "")
    request = ET.Element("modify_target", target_id=target_id)
    if args.set_name:
        ET.SubElement(request, "name").text = args.set_name
    if args.hosts:
        ET.SubElement(request, "hosts").text = args.hosts
    if args.exclude_hosts:
        ET.SubElement(request, "exclude_hosts").text = args.exclude_hosts
    if args.credential:
        credential_id = _resolve_resource_id(runner, "get_credentials", "credential", args.credential)
        ET.SubElement(request, "smb_credential", id=credential_id)
    if args.port_list:
        port_list_id = _resolve_resource_id(runner, "get_port_lists", "port_list", args.port_list)
        ET.SubElement(request, "port_list", id=port_list_id)
    if len(request) == 0:
        raise OpenvasCliError("target update requires at least one mutable field")
    response = runner.invoke_xml(request)
    _json_print({"id": target_id, "status": response.status, "status_text": response.status_text})


def command_task_list(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    request = _make_simple_request("get_tasks")
    if args.filter:
        request.set("filter", args.filter)
    if args.details:
        request.set("details", "1")
    response = runner.invoke_xml(request)
    _json_print({"tasks": [_task_json(node) for node in response.direct_children("task")]})


def command_task_get(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_tasks", "task", value_type, value, details=True)
    _json_print({"task": _task_json(node)})


def command_task_create(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    _json_print(_create_task(args, runner))


def command_task_update(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_tasks", "task", value_type, value, details=True)
    task_id = node.attrib.get("id", "")
    request = ET.Element("modify_task", task_id=task_id)
    if args.set_name:
        ET.SubElement(request, "name").text = args.set_name
    if args.target:
        target_id = _resolve_resource_id(runner, "get_targets", "target", args.target)
        ET.SubElement(request, "target", id=target_id)
    if args.scan_config:
        config_id = _resolve_resource_id(runner, "get_configs", "config", args.scan_config)
        ET.SubElement(request, "config", id=config_id)
    if args.scanner:
        scanner_id = _resolve_resource_id(runner, "get_scanners", "scanner", args.scanner)
        ET.SubElement(request, "scanner", id=scanner_id)
    if len(request) == 0:
        raise OpenvasCliError("task update requires at least one mutable field")
    response = runner.invoke_xml(request)
    _json_print({"id": task_id, "status": response.status, "status_text": response.status_text})


def _task_lookup_id(args: argparse.Namespace, runner: GvmCliRunner) -> str:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_tasks", "task", value_type, value, details=True)
    return node.attrib.get("id", "")


def command_task_start(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    task_id = _task_lookup_id(args, runner)
    response = runner.invoke_xml(_make_simple_request("start_task", task_id=task_id))
    _json_print({
        "task_id": task_id,
        "report_id": response.root.findtext("report_id", default=""),
        "status": response.status,
        "status_text": response.status_text,
    })


def command_task_stop(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    task_id = _task_lookup_id(args, runner)
    response = runner.invoke_xml(_make_simple_request("stop_task", task_id=task_id))
    _json_print({"task_id": task_id, "status": response.status, "status_text": response.status_text})


def command_task_resume(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    task_id = _task_lookup_id(args, runner)
    response = runner.invoke_xml(_make_simple_request("resume_task", task_id=task_id))
    _json_print({
        "task_id": task_id,
        "report_id": response.root.findtext("report_id", default=""),
        "status": response.status,
        "status_text": response.status_text,
    })


def command_report_list(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    request = _make_simple_request("get_reports")
    if args.filter:
        request.set("filter", args.filter)
    response = runner.invoke_xml(request)
    _json_print({"reports": [_report_json(node) for node in response.direct_children("report")]})


def command_report_get(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    format_value = args.format.lower()
    format_id = XML_REPORT_FORMAT_ID if format_value == "xml" else PDF_REPORT_FORMAT_ID if format_value == "pdf" else args.format
    request = _make_simple_request("get_reports", report_id=args.id)
    if format_id:
        request.set("format_id", format_id)
    response = runner.invoke_xml(request)
    report = response.first("report")
    if report is None:
        raise OpenvasCliError(f"report not found: {args.id}")

    if format_id == XML_REPORT_FORMAT_ID:
        if args.output:
            Path(args.output).write_text(response.raw_xml, encoding="utf-8")
            _json_print({"id": args.id, "output": args.output, "format_id": format_id})
            return
        sys.stdout.write(response.raw_xml)
        if not response.raw_xml.endswith("\n"):
            sys.stdout.write("\n")
        return

    if not args.output:
        raise OpenvasCliError("binary report export requires --output")
    payload = "".join(report.itertext()).strip()
    Path(args.output).write_bytes(b64decode(payload.encode("ascii")))
    _json_print({"id": args.id, "output": args.output, "format_id": format_id})


def _combine_filter(base: str, extra: str) -> str:
    base = (base or "").strip()
    extra = (extra or "").strip()
    if base and extra:
        return f"{base} {extra}"
    return base or extra


def _command_list_named_resource(
    runner: GvmCliRunner,
    command_name: str,
    node_name: str,
    filter_value: Optional[str],
    *,
    extra_filter: str = "",
    extra_attributes: Optional[Dict[str, str]] = None,
    serializer=None,
) -> None:
    request = _make_simple_request(command_name)
    combined_filter = _combine_filter(filter_value or "", extra_filter)
    if combined_filter:
        request.set("filter", combined_filter)
    for key, value in (extra_attributes or {}).items():
        if value:
            request.set(key, value)
    response = runner.invoke_xml(request)
    key = {
        "config": "configs",
        "scanner": "scanners",
        "credential": "credentials",
        "report_format": "report_formats",
    }[node_name]
    render = serializer or _generic_named_json
    _json_print({key: [render(node) for node in response.direct_children(node_name)]})


def command_credential_get(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_credentials", "credential", value_type, value, details=args.details)
    _json_print({"credential": _credential_json(node, include_details=args.details)})


def _prompt_password(prompt: str, default: str = "") -> str:
    value = getpass.getpass(f"{prompt}: ")
    if not value:
        value = default
    return value


def command_credential_create(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    cred_type = args.type
    if cred_type == "up":
        if not args.username:
            raise OpenvasCliError("credential create --type up requires --username")
        if not args.password:
            args.password = _prompt_password("Password")
        if not args.password:
            raise OpenvasCliError("password is required")
        request = ET.Element("create_credential")
        ET.SubElement(request, "name").text = args.name
        ET.SubElement(request, "type").text = "up"
        ET.SubElement(request, "login").text = args.username
        ET.SubElement(request, "password").text = args.password
        if args.comment:
            ET.SubElement(request, "comment").text = args.comment

    elif cred_type == "usk":
        if not args.username:
            raise OpenvasCliError("credential create --type usk requires --username")
        if not args.private_key:
            raise OpenvasCliError("credential create --type usk requires --private-key")
        private_key_path = Path(args.private_key).expanduser()
        if not private_key_path.exists():
            raise OpenvasCliError(f"private key file not found: {private_key_path}")
        private_key_content = private_key_path.read_text(encoding="utf-8")
        passphrase = args.password or ""
        if not passphrase and args.passphrase:
            passphrase = args.passphrase
        if not passphrase and args.private_key:
            passphrase = _prompt_password("SSH key passphrase (or press Enter if none)")
        request = ET.Element("create_credential")
        ET.SubElement(request, "name").text = args.name
        ET.SubElement(request, "type").text = "usk"
        ET.SubElement(request, "login").text = args.username
        key_elem = ET.SubElement(request, "key")
        if passphrase:
            ET.SubElement(key_elem, "phrase").text = passphrase
        ET.SubElement(key_elem, "private").text = private_key_content
        if args.comment:
            ET.SubElement(request, "comment").text = args.comment

    elif cred_type == "snmp":
        has_community = args.community and args.community.strip()
        has_v3 = args.snmp_username and args.snmp_auth_password
        if not has_community and not has_v3:
            raise OpenvasCliError("credential create --type snmp requires --community (v1/v2) or --snmp-username and --snmp-auth-password (v3)")
        request = ET.Element("create_credential")
        ET.SubElement(request, "name").text = args.name
        ET.SubElement(request, "type").text = "snmp"
        if has_community:
            ET.SubElement(request, "community").text = args.community
        if has_v3:
            ET.SubElement(request, "login").text = args.snmp_username
            auth_pwd = args.snmp_auth_password or _prompt_password("SNMP auth password")
            if auth_pwd:
                ET.SubElement(request, "password").text = auth_pwd
            if args.snmp_auth_protocol:
                ET.SubElement(request, "auth_algorithm").text = args.snmp_auth_protocol
            if args.snmp_priv_password:
                privacy_elem = ET.SubElement(request, "privacy")
                alg = args.snmp_priv_protocol if args.snmp_priv_protocol and args.snmp_priv_protocol != "none" else "aes"
                ET.SubElement(privacy_elem, "algorithm").text = alg
                ET.SubElement(privacy_elem, "password").text = args.snmp_priv_password
        if args.comment:
            ET.SubElement(request, "comment").text = args.comment

    else:
        raise OpenvasCliError(f"unsupported credential type: {cred_type}")

    response = runner.invoke_xml(request)
    _json_print({
        "id": response.root.attrib.get("id", ""),
        "status": response.status,
        "status_text": response.status_text,
    })


def command_credential_update(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_credentials", "credential", value_type, value, details=True)
    credential_id = node.attrib.get("id", "")
    cred_type = node.findtext("type", default="")

    request = ET.Element("modify_credential", credential_id=credential_id)
    if args.set_name:
        ET.SubElement(request, "name").text = args.set_name
    if args.comment is not None:
        ET.SubElement(request, "comment").text = args.comment or ""

    if cred_type == "up":
        if args.username:
            ET.SubElement(request, "login").text = args.username
        if args.password:
            ET.SubElement(request, "password").text = args.password

    elif cred_type == "usk":
        if args.username:
            ET.SubElement(request, "login").text = args.username
        if args.private_key:
            private_key_path = Path(args.private_key).expanduser()
            if not private_key_path.exists():
                raise OpenvasCliError(f"private key file not found: {private_key_path}")
            private_key_content = private_key_path.read_text(encoding="utf-8")
            key_elem = ET.SubElement(request, "key")
            passphrase = args.password or args.passphrase or ""
            if passphrase:
                ET.SubElement(key_elem, "phrase").text = passphrase
            ET.SubElement(key_elem, "private").text = private_key_content

    elif cred_type == "snmp":
        if args.community:
            ET.SubElement(request, "community").text = args.community
        if args.snmp_username:
            ET.SubElement(request, "login").text = args.snmp_username
        if args.snmp_auth_password:
            ET.SubElement(request, "password").text = args.snmp_auth_password
        if args.snmp_auth_protocol:
            ET.SubElement(request, "auth_algorithm").text = args.snmp_auth_protocol
        if args.snmp_priv_password:
            privacy_elem = ET.SubElement(request, "privacy")
            alg = args.snmp_priv_protocol if args.snmp_priv_protocol and args.snmp_priv_protocol != "none" else "aes"
            ET.SubElement(privacy_elem, "algorithm").text = alg
            ET.SubElement(privacy_elem, "password").text = args.snmp_priv_password

    if len(request) <= 1:
        raise OpenvasCliError("credential update requires at least one field to update")

    response = runner.invoke_xml(request)
    _json_print({
        "id": credential_id,
        "status": response.status,
        "status_text": response.status_text,
    })


def command_credential_delete(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    value_type, value = _require_lookup(args)
    node = _lookup_direct_child(runner, "get_credentials", "credential", value_type, value, details=True)
    credential_id = node.attrib.get("id", "")
    in_use = _text_to_bool(node.findtext("in_use", default=""))

    if in_use and not args.force:
        raise OpenvasCliError(f"credential is in use by targets. Remove from targets or use --force to delete anyway")

    request = ET.Element("delete_credential", credential_id=credential_id)
    response = runner.invoke_xml(request)
    _json_print({
        "id": credential_id,
        "status": response.status,
        "status_text": response.status_text,
    })


def command_scan_create(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    target_name = args.target_name or f"Target {args.hosts}"
    task_name = args.task_name or f"Scan {args.hosts}"

    try:
        target = _lookup_direct_child(runner, "get_targets", "target", "name", target_name, details=True, tasks=True)
        target_id = target.attrib.get("id", "")
        target_created = False
    except OpenvasCliError:
        if not args.port_list and not args.port_range:
            raise OpenvasCliError("scan create needs --port-list or --port-range when target does not exist")
        _create_target(
            argparse.Namespace(
                name=target_name,
                hosts=args.hosts,
                exclude_hosts=None,
                credential=args.credential,
                port_list=args.port_list,
                port_range=args.port_range,
            ),
            runner,
        )
        target = _lookup_direct_child(runner, "get_targets", "target", "name", target_name, details=True, tasks=True)
        target_id = target.attrib.get("id", "")
        target_created = True

    desired_credential_id = _resolve_resource_id(runner, "get_credentials", "credential", args.credential) if args.credential else ""
    desired_port_list_id = _resolve_resource_id(runner, "get_port_lists", "port_list", args.port_list) if args.port_list else ""

    if not target_created:
        target_changed = False
        request = ET.Element("modify_target", target_id=target_id)
        if target.findtext("hosts", default="") != args.hosts:
            ET.SubElement(request, "hosts").text = args.hosts
            target_changed = True
        current_credential_id = _child_attr(target, "smb_credential", "id")
        if desired_credential_id and current_credential_id != desired_credential_id:
            ET.SubElement(request, "smb_credential", id=desired_credential_id)
            target_changed = True
        current_port_list_id = _child_attr(target, "port_list", "id")
        if desired_port_list_id and current_port_list_id != desired_port_list_id:
            ET.SubElement(request, "port_list", id=desired_port_list_id)
            target_changed = True
        if target_changed:
            runner.invoke_xml(request)
            target = _lookup_direct_child(runner, "get_targets", "target", "name", target_name, details=True, tasks=True)

    config_id = _resolve_resource_id(runner, "get_configs", "config", args.scan_config)
    scanner_id = _resolve_resource_id(runner, "get_scanners", "scanner", args.scanner)

    try:
        task = _lookup_direct_child(runner, "get_tasks", "task", "name", task_name, details=True)
        task_id = task.attrib.get("id", "")
        task_created = False
    except OpenvasCliError:
        _create_task(
            argparse.Namespace(name=task_name, target=target_id, scan_config=args.scan_config, scanner=args.scanner),
            runner,
        )
        task = _lookup_direct_child(runner, "get_tasks", "task", "name", task_name, details=True)
        task_id = task.attrib.get("id", "")
        task_created = True

    if not task_created:
        task_changed = False
        request = ET.Element("modify_task", task_id=task_id)
        if _child_attr(task, "target", "id") != target_id:
            ET.SubElement(request, "target", id=target_id)
            task_changed = True
        if _child_attr(task, "config", "id") != config_id:
            ET.SubElement(request, "config", id=config_id)
            task_changed = True
        if _child_attr(task, "scanner", "id") != scanner_id:
            ET.SubElement(request, "scanner", id=scanner_id)
            task_changed = True
        if task_changed:
            runner.invoke_xml(request)
            task = _lookup_direct_child(runner, "get_tasks", "task", "name", task_name, details=True)

    status = task.findtext("status", default="")
    if status in {"Running", "Requested", "Processing"}:
        _json_print({
            "target": _target_json(target),
            "task": _task_json(task),
            "report_id": _deep_attr(task, ["last_report", "report"], "id"),
            "started": False,
        })
        return

    start_response = runner.invoke_xml(_make_simple_request("start_task", task_id=task_id))
    task = _lookup_direct_child(runner, "get_tasks", "task", "id", task_id, details=True)
    _json_print({
        "target": _target_json(target),
        "task": _task_json(task),
        "report_id": start_response.root.findtext("report_id", default=""),
        "started": True,
    })


def dispatch(args: argparse.Namespace, runner: GvmCliRunner) -> None:
    if args.resource == "onboard":
        command_onboard(args, runner)
        return
    if args.resource == "doctor":
        command_doctor(args, runner)
        return
    if args.resource == "system" and args.action == "version":
        command_system_version(args, runner)
        return
    if args.resource == "target" and args.action == "list":
        command_target_list(args, runner)
        return
    if args.resource == "target" and args.action == "get":
        command_target_get(args, runner)
        return
    if args.resource == "target" and args.action == "create":
        command_target_create(args, runner)
        return
    if args.resource == "target" and args.action == "update":
        command_target_update(args, runner)
        return
    if args.resource == "task" and args.action == "list":
        command_task_list(args, runner)
        return
    if args.resource == "task" and args.action == "get":
        command_task_get(args, runner)
        return
    if args.resource == "task" and args.action == "create":
        command_task_create(args, runner)
        return
    if args.resource == "task" and args.action == "update":
        command_task_update(args, runner)
        return
    if args.resource == "task" and args.action == "start":
        command_task_start(args, runner)
        return
    if args.resource == "task" and args.action == "stop":
        command_task_stop(args, runner)
        return
    if args.resource == "task" and args.action == "resume":
        command_task_resume(args, runner)
        return
    if args.resource == "report" and args.action == "list":
        command_report_list(args, runner)
        return
    if args.resource == "report" and args.action == "get":
        command_report_get(args, runner)
        return
    if args.resource == "config" and args.action == "list":
        _command_list_named_resource(
            runner,
            "get_configs",
            "config",
            args.filter,
            extra_filter="sort=name rows=-1",
            extra_attributes={
                "usage_type": "scan",
                "details": "1" if args.details else "",
                "tasks": "1" if args.tasks else "",
                "preferences": "1" if args.preferences else "",
            },
            serializer=lambda node: _config_json(
                node,
                include_details=args.details,
                include_tasks=args.tasks,
                include_preferences=args.preferences,
            ),
        )
        return
    if args.resource == "config" and args.action == "get":
        value_type, value = _require_lookup(args)
        node = _lookup_direct_child(
            runner,
            "get_configs",
            "config",
            value_type,
            value,
            details=args.details,
            tasks=args.tasks,
        )
        if args.preferences and not args.details:
            request = _make_simple_request(
                "get_configs",
                config_id=node.attrib.get("id", ""),
                usage_type="scan",
                preferences="1",
                tasks="1" if args.tasks else "",
            )
            response = runner.invoke_xml(request)
            direct = response.direct_children("config")
            if direct:
                node = direct[0]
        _json_print({
            "config": _config_json(
                node,
                include_details=args.details,
                include_tasks=args.tasks,
                include_preferences=args.preferences,
            )
        })
        return

    if args.resource == "scanner" and args.action == "list":
        _command_list_named_resource(runner, "get_scanners", "scanner", args.filter)
        return
    if args.resource == "credential" and args.action == "list":
        _command_list_named_resource(runner, "get_credentials", "credential", args.filter)
        return
    if args.resource == "credential" and args.action == "get":
        command_credential_get(args, runner)
        return
    if args.resource == "credential" and args.action == "create":
        command_credential_create(args, runner)
        return
    if args.resource == "credential" and args.action == "update":
        command_credential_update(args, runner)
        return
    if args.resource == "credential" and args.action == "delete":
        command_credential_delete(args, runner)
        return
    if args.resource == "report-format" and args.action == "list":
        _command_list_named_resource(runner, "get_report_formats", "report_format", args.filter)
        return
    if args.resource == "scan" and args.action == "create":
        command_scan_create(args, runner)
        return
    raise OpenvasCliError("unsupported command")


def main() -> int:
    global JSON_INDENT
    parser = _build_parser()
    args = parser.parse_args()
    if not args.resource:
        parser.print_help()
        return 0
    if args.compact_json:
        JSON_INDENT = None
    runner = GvmCliRunner(args)
    tunnel = None
    if args.resource != "onboard":
        jump_host = runner.env_value("OPENVAS_JUMP_HOST")
        if jump_host:
            jump_port = runner.env_value("OPENVAS_JUMP_PORT") or DEFAULT_SSH_PORT
            jump_username = runner.env_value("OPENVAS_JUMP_SSH_USERNAME")
            if not jump_username:
                raise OpenvasCliError("OPENVAS_JUMP_SSH_USERNAME is required when OPENVAS_JUMP_HOST is set")
            target_host = runner.env_value("OPENVAS_HOST")
            target_port = runner.env_value("OPENVAS_PORT") or "22"
            ssh_identity_file = runner.env_value("OPENVAS_SSH_IDENTITY_FILE") or None
            tunnel = JumpHostTunnel(
                jump_host=jump_host,
                jump_port=jump_port,
                jump_username=jump_username,
                target_host=target_host,
                target_port=target_port,
                ssh_identity_file=ssh_identity_file,
            )
            tunnel.open()
            runner.env["OPENVAS_HOST"] = "127.0.0.1"
            runner.env["OPENVAS_PORT"] = str(tunnel.local_port)
            if not args.hostname:
                args.hostname = "127.0.0.1"
            if not args.port:
                args.port = tunnel.local_port
    try:
        dispatch(args, runner)
        return 0
    except OpenvasCliError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if tunnel is not None:
            tunnel.close()


if __name__ == "__main__":
    sys.exit(main())
