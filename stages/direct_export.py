# coding: utf-8
"""Stage 4b (for disks > 1 TiB): Direct export via SSH + qemu-img + obsutil.

When the restored volume exceeds the 1 TiB IMS image limit, we bypass IMS
entirely: SSH into the ECS, convert the data disk to VHD with qemu-img,
and upload directly to OBS using obsutil.
"""

from __future__ import annotations

import logging
import time
import io

import paramiko

logger = logging.getLogger(__name__)

_SSH_TIMEOUT = 30
_CMD_TIMEOUT = 600

_OBSUTIL_URL = (
    "https://obs-community-intl.obs.intl.myhuaweicloud.com"
    "/obsutil/current/obsutil_linux_amd64.tar.gz"
)


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


def _run_cmd(ssh: paramiko.SSHClient, cmd: str, timeout: int = _CMD_TIMEOUT) -> str:
    """Run a command over SSH, stream output to logger, return stdout."""
    logger.info("  $ %s", cmd)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if out.strip():
        for line in out.strip().split("\n"):
            logger.info("    %s", line)
    if err.strip():
        for line in err.strip().split("\n"):
            logger.info("    [stderr] %s", line)
    if exit_code != 0:
        raise RuntimeError(f"Command failed (exit {exit_code}): {cmd}\nstderr: {err}")
    return out


def _install_qemu_img(ssh: paramiko.SSHClient) -> None:
    """Install qemu-img on the ECS (CentOS)."""
    _run_cmd(ssh, "yum install -y qemu-img 2>&1 || yum install -y qemu-kvm 2>&1", timeout=120)


def _install_obsutil(ssh: paramiko.SSHClient) -> None:
    """Download and install obsutil on the ECS."""
    cmds = [
        f"cd /tmp && wget -q '{_OBSUTIL_URL}' -O obsutil.tar.gz",
        "cd /tmp && tar xzf obsutil.tar.gz",
        "chmod +x /tmp/obsutil_linux_amd64_*/obsutil",
        "ln -sf /tmp/obsutil_linux_amd64_*/obsutil /usr/local/bin/obsutil",
    ]
    for cmd in cmds:
        _run_cmd(ssh, cmd, timeout=60)
    _run_cmd(ssh, "obsutil version", timeout=10)


def _configure_obsutil(ssh: paramiko.SSHClient, ak: str, sk: str, region: str) -> None:
    """Configure obsutil with credentials and endpoint."""
    endpoint = f"obs.{region}.myhuaweicloud.com"
    _run_cmd(ssh, f"obsutil config -i={ak} -k={sk} -e={endpoint}", timeout=15)


def _find_data_disk(ssh: paramiko.SSHClient) -> str:
    """Find the attached data disk device (typically /dev/vdb or /dev/xvdb)."""
    out = _run_cmd(ssh, "lsblk -b -d -o NAME,SIZE,TYPE | grep disk | sort -k2 -n")
    lines = [l for l in out.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"Expected system + data disk, found: {lines}")
    parts = lines[1].split()
    device = f"/dev/{parts[0]}"
    logger.info("Data disk device: %s", device)
    return device


def direct_export_to_obs(
    public_ip: str,
    private_key_pem: str,
    ak: str,
    sk: str,
    region: str,
    bucket_name: str,
    object_key: str,
    poll_interval: int = 15,
) -> bool:
    """SSH into ECS, convert data disk to VHD, upload to OBS. Returns True on success.

    Steps:
    1. SSH connect
    2. Install qemu-img + obsutil
    3. Configure obsutil with AK/SK
    4. qemu-img convert -f raw -O vhd /dev/vdb /tmp/export.vhd
    5. obsutil cp /tmp/export.vhd obs://bucket/key
    6. Verify object exists
    7. Clean up temp file
    """
    ssh = _ssh_connect(public_ip, private_key_pem)
    try:
        logger.info("[4b/6] Instalando qemu-img y obsutil en ECS...")
        _install_qemu_img(ssh)
        _install_obsutil(ssh)
        _configure_obsutil(ssh, ak, sk, region)

        device = _find_data_disk(ssh)

        logger.info("[5b/6] Convirtiendo disco a VHD con qemu-img...")
        vhd_path = "/tmp/export.vhd"
        _run_cmd(
            ssh,
            f"qemu-img convert -p -f raw -O vhd {device} {vhd_path}",
            timeout=7200,
        )

        size_out = _run_cmd(ssh, f"ls -lh {vhd_path} | awk '{{print $5}}'")
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
            timeout=30,
        )
        if object_key not in list_out:
            logger.warning("Object key not found in obsutil ls output")
            return False

        logger.info("Limpiando archivo temporal en ECS...")
        _run_cmd(ssh, f"rm -f {vhd_path}", timeout=10)

        logger.info("Export directo completado: obs://%s/%s", bucket_name, object_key)
        return True

    finally:
        ssh.close()
