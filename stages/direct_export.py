# coding: utf-8
"""Stage 4b (for disks > 1 TiB): Direct export via SSH + qemu-img + obsutil.

When the restored volume exceeds the 1 TiB IMS image limit, we bypass IMS
entirely: SSH into the ECS, convert the data disk to VHD with qemu-img,
and upload directly to OBS using obsutil.

The VHD is written to a scratch data disk attached to the helper ECS
(the 40 GB root volume cannot hold it). Disk roles are identified by
attach order: vda=root, vdb=restored data disk, vdc=scratch.
"""

from __future__ import annotations

import io
import logging
import socket
import threading
import time

import paramiko

logger = logging.getLogger(__name__)

_SSH_TIMEOUT = 30
_CMD_TIMEOUT = 600

_OBSUTIL_URL = (
    "https://obs-community-intl.obs.intl.myhuaweicloud.com"
    "/obsutil/current/obsutil_linux_amd64.tar.gz"
)

_SCRATCH_MOUNT = "/mnt/scratch"


def _ssh_connect(public_ip: str, private_key_pem: str) -> paramiko.SSHClient:
    """Connect to ECS via SSH using the generated private key."""
    key = paramiko.RSAKey.from_private_key(io.StringIO(private_key_pem))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    logger.info("Connecting via SSH to %s ...", public_ip)
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            client.connect(
                hostname=public_ip,
                port=22,
                username="root",
                pkey=key,
                timeout=_SSH_TIMEOUT,
            )
            logger.info("SSH connected to %s", public_ip)
            return client
        except Exception as e:
            logger.info("  SSH not ready yet (%s), retrying...", e)
            time.sleep(5)
    raise RuntimeError(f"Could not SSH to {public_ip} after 120s")


def _tail(text: str, n: int = 15) -> str:
    """Last n non-empty lines of an output (for error messages)."""
    lines = [l for l in text.replace("\r", "\n").split("\n") if l.strip()]
    return "\n".join(lines[-n:]) if lines else "(sin salida)"


def _run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: int = _CMD_TIMEOUT) -> str:
    """Run a command over SSH, streaming output to the log. Returns stdout.

    - stderr is drained in a thread and logged live (yum/dnf put real
      errors there; without draining, a full buffer deadlocks the command).
    - stdout is logged sampled (first 10 lines + every 50th) so chatty
      commands (qemu-img convert -p) don't flood the log, while short
      outputs still show completely.
    - The channel timeout is per-read idle, NOT total duration: commands
      that keep producing output can run for hours.
    - On failure the error includes the tail of BOTH streams (yum prints
      many errors to stdout, which previously got lost).
    """
    logger.info("  $ %s", cmd)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)

    err_buf: list[str] = []

    def _drain_stderr() -> None:
        try:
            for raw in iter(stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                err_buf.append(line)
                logger.info("    [stderr] %s", line.rstrip())
        except Exception:
            pass

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    out_buf: list[str] = []
    try:
        for raw in iter(stdout.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            out_buf.append(line)
            n = len(out_buf)
            if n <= 10 or n % 50 == 0:
                logger.info("    %s", line.rstrip())
    except socket.timeout:
        raise RuntimeError(
            f"Sin output del comando por {timeout}s (timeout del canal SSH): {cmd}"
        )

    exit_code = stdout.channel.recv_exit_status()
    err_thread.join(timeout=10)

    out = "".join(out_buf)
    err = "".join(err_buf)
    if len(out_buf) > 10:
        logger.info("    ... (%d lineas de stdout, ultimas 5:)", len(out_buf))
        for line in out_buf[-5:]:
            logger.info("    %s", line.rstrip())

    if exit_code != 0:
        raise RuntimeError(
            f"Command failed (exit {exit_code}): {cmd}\n"
            f"--- stdout (tail) ---\n{_tail(out)}\n"
            f"--- stderr (tail) ---\n{_tail(err)}"
        )
    return out


def _try_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: int) -> bool:
    """Run a command, returning True on success, False (logged) on failure."""
    try:
        _run_cmd(ssh, cmd, timeout=timeout)
        return True
    except RuntimeError as e:
        logger.warning("  Paso fallo — probando siguiente alternativa:\n%s", e)
        return False


def _install_qemu_img(ssh: paramiko.SSHClient) -> None:
    """Install qemu-img on the helper ECS, one strategy at a time.

    Order: already present -> stock yum repos (Huawei images point at
    repo.huaweicloud.com, which still serves CentOS 7) -> deterministic
    vault.centos.org repo file (CentOS 7 EOL fallback) -> qemu-kvm
    (pulls qemu-img as a dependency) -> apt. Each failure is logged with
    its full output; the final verification raises a clear error.
    """
    logger.info("  Diagnostico del OS de la ECS auxiliar:")
    _run_cmd(
        ssh,
        "head -2 /etc/os-release 2>/dev/null; ls /etc/yum.repos.d/ 2>/dev/null | head -10",
        timeout=30,
    )

    if _try_cmd(ssh, "qemu-img --version", timeout=30):
        return

    steps = [
        ("yum con repos originales", "yum install -y qemu-img", 600),
        (
            "repo vault.centos.org (fallback CentOS 7 EOL)",
            "if grep -q 'ID=\"centos\"' /etc/os-release && grep -q 'VERSION_ID=\"7\"' /etc/os-release; then "
            "mkdir -p /etc/yum.repos.d/orig && "
            "mv -f /etc/yum.repos.d/*.repo /etc/yum.repos.d/orig/ 2>/dev/null; "
            "printf '[vault]\\nname=CentOS-7 Vault\\n"
            "baseurl=http://vault.centos.org/7.9.2009/os/x86_64/\\n"
            "enabled=1\\ngpgcheck=0\\n' > /etc/yum.repos.d/vault.repo; "
            "yum clean all && yum install -y qemu-img; else exit 1; fi",
            600,
        ),
        ("qemu-kvm (incluye qemu-img como dependencia)", "yum install -y qemu-kvm", 600),
        ("apt-get (Debian/Ubuntu)", "apt-get update && apt-get install -y qemu-utils", 600),
    ]
    for desc, cmd, tmo in steps:
        logger.info("  Instalando qemu-img via %s...", desc)
        if _try_cmd(ssh, cmd, tmo):
            break

    try:
        _run_cmd(ssh, "qemu-img --version", timeout=30)
    except RuntimeError as e:
        raise RuntimeError(
            "qemu-img no quedo instalado tras agotar todas las alternativas "
            "(revisar la salida de yum/apt arriba en el log). Detalle del ultimo intento:\n" + str(e)
        )


def _install_obsutil(ssh: paramiko.SSHClient) -> None:
    """Download and install obsutil on the ECS.

    wget runs WITHOUT -q on purpose: its progress dots keep the SSH
    channel alive during long downloads.
    """
    cmds = [
        (
            f"cd /tmp && (wget '{_OBSUTIL_URL}' -O obsutil.tar.gz "
            f"|| curl -fL '{_OBSUTIL_URL}' -o obsutil.tar.gz)",
            600,
        ),
        ("cd /tmp && tar xzf obsutil.tar.gz", 120),
        ("chmod +x /tmp/obsutil_linux_amd64_*/obsutil", 30),
        ("ln -sf /tmp/obsutil_linux_amd64_*/obsutil /usr/local/bin/obsutil", 30),
        ("obsutil version", 30),
    ]
    for cmd, tmo in cmds:
        _run_cmd(ssh, cmd, timeout=tmo)


def _configure_obsutil(ssh: paramiko.SSHClient, ak: str, sk: str, region: str) -> None:
    """Configure obsutil with credentials and endpoint."""
    endpoint = f"obs.{region}.myhuaweicloud.com"
    _run_cmd(ssh, f"obsutil config -i={ak} -k={sk} -e={endpoint}", timeout=30)


def _list_disks(ssh: paramiko.SSHClient) -> list[tuple[str, int]]:
    """Disks in attach order: [(name, size_bytes), ...]."""
    out = _run_cmd(ssh, 'lsblk -b -d -o NAME,SIZE,TYPE | awk \'$3=="disk"{print $1, $2}\'')
    disks: list[tuple[str, int]] = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            disks.append((parts[0], int(parts[1])))
    return disks


def _wait_for_disks(
    ssh: paramiko.SSHClient,
    expected_data_gb: int | None = None,
    want_scratch: bool = True,
    timeout: int = 180,
) -> dict[str, tuple[str, int]]:
    """Wait for the expected disks and map roles by attach order.

    vda=root (created with the ECS), vdb=restored data disk (attached
    first), vdc=scratch (attached second). A size sanity check guards
    against unexpected attach orders.
    """
    needed = 3 if want_scratch else 2
    deadline = time.time() + timeout
    disks: list[tuple[str, int]] = []
    while time.time() < deadline:
        disks = _list_disks(ssh)
        if len(disks) >= needed:
            break
        logger.info("  Discos visibles: %s — esperando attach...", disks)
        time.sleep(10)
    if len(disks) < needed:
        raise RuntimeError(
            f"Se esperaban {needed} discos (root + data + scratch) pero hay "
            f"{len(disks)}: {disks}"
        )
    if expected_data_gb:
        data_gb = disks[1][1] / 1e9
        if abs(data_gb - expected_data_gb) > expected_data_gb * 0.05:
            raise RuntimeError(
                f"El segundo disco mide {data_gb:.0f}GB pero se esperaban "
                f"~{expected_data_gb}GB (orden de attach inesperado): {disks}"
            )
    roles = {"root": disks[0], "data": disks[1]}
    if len(disks) >= 3:
        roles["scratch"] = disks[2]
    logger.info("Discos: root=%s data=%s scratch=%s",
                disks[0], disks[1], roles.get("scratch"))
    return roles


def direct_export_to_obs(
    public_ip: str,
    private_key_pem: str,
    ak: str,
    sk: str,
    region: str,
    bucket_name: str,
    object_key: str,
    expected_data_gb: int | None = None,
    poll_interval: int = 15,
) -> bool:
    """SSH into ECS, convert data disk to VHD on the scratch disk, upload to OBS.

    Steps:
    1. SSH connect
    2. Install qemu-img + obsutil
    3. Configure obsutil with AK/SK
    4. mkfs + mount scratch disk (vdc)
    5. qemu-img convert -f raw -O vhd /dev/vdb <scratch>/export.vhd
    6. obsutil cp to OBS + verify
    """
    ssh = _ssh_connect(public_ip, private_key_pem)
    try:
        logger.info("[4b/6] Instalando qemu-img y obsutil en ECS...")
        _install_qemu_img(ssh)
        _install_obsutil(ssh)
        _configure_obsutil(ssh, ak, sk, region)

        roles = _wait_for_disks(ssh, expected_data_gb=expected_data_gb)
        data_dev = f"/dev/{roles['data'][0]}"
        scratch_dev = f"/dev/{roles['scratch'][0]}"

        logger.info("[5b/6] Preparando disco scratch %s (mkfs + mount)...", scratch_dev)
        _run_cmd(
            ssh,
            f"mkfs.ext4 -F {scratch_dev} && mkdir -p {_SCRATCH_MOUNT} "
            f"&& mount {scratch_dev} {_SCRATCH_MOUNT}",
            timeout=600,
        )
        _run_cmd(ssh, f"df -h {_SCRATCH_MOUNT}", timeout=30)

        vhd_path = f"{_SCRATCH_MOUNT}/export.vhd"
        logger.info("[5b/6] Convirtiendo %s a VHD (puede tardar horas)...", data_dev)
        _run_cmd(
            ssh,
            f"qemu-img convert -p -f raw -O vhd {data_dev} {vhd_path}",
            timeout=7200,
        )

        size_out = _run_cmd(ssh, f"ls -lh {vhd_path} | awk '{{print $5}}'", timeout=30)
        logger.info("VHD file size: %s", size_out.strip())

        logger.info("[6b/6] Subiendo VHD a OBS con obsutil...")
        _run_cmd(
            ssh,
            f"obsutil cp {vhd_path} obs://{bucket_name}/{object_key} -f",
            timeout=7200,
        )

        logger.info("Verificando objeto en OBS...")
        list_out = _run_cmd(
            ssh,
            f"obsutil ls obs://{bucket_name}/{object_key} -d -limit=1",
            timeout=60,
        )
        if object_key not in list_out:
            logger.warning("Object key not found in obsutil ls output")
            return False

        _run_cmd(ssh, f"rm -f {vhd_path}", timeout=60)

        logger.info("Export directo completado: obs://%s/%s", bucket_name, object_key)
        return True

    finally:
        ssh.close()
