# coding: utf-8
"""Stage 4b (for disks > 1 TiB): Direct export via SSH + qemu-img + presigned PUT.

When the restored volume exceeds the 1 TiB IMS image limit, we bypass IMS
entirely: SSH into the ECS, convert the data disk to VHD with qemu-img on
a scratch disk, and upload with curl using an OBS presigned URL.

Design notes:
- The VHD is written to a scratch data disk (the 40 GB root volume cannot
  hold it). Disk roles by attach order: vda=root, vdb=data, vdc=scratch.
- The upload targets the INTERNAL OBS endpoint (obs.<region>.internal.
  myhuaweicloud.com): intra-region traffic, no EIP bandwidth limit (the
  public endpoint would cap a 1 TiB upload at the EIP's 5 Mbps ~ weeks).
- The presigned URL is computed on the laptop (OBS V2 signature); the AK
  signs the request and the SK never reaches the ECS. URLs are redacted
  from logs. This replaces obsutil entirely: no external downloads, no
  credentials on the box.
- A HEAD pre-flight runs BEFORE the multi-hour convert: DNS, TLS and the
  signature against the internal endpoint are validated in seconds
  (404 = object not there yet, which is what we want).
- SSH resilience: the whole run takes hours over the public internet, so
  the TCP connection WILL drop at some point (NAT idle timeout, proxy,
  Wi-Fi blip). Three layers of defense:
  1. Transport keepalives every 30s (keeps NAT mappings alive).
  2. _SshSession: every command goes through an auto-reconnecting
     wrapper; quick commands are retried once on a mid-command drop.
  3. The multi-hour steps (qemu-img convert, curl PUT) run DETACHED on
     the ECS (nohup + exit-code file) and are monitored by polling with
     short commands: an SSH drop only interrupts the monitoring, never
     the remote work itself.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import shlex
import socket
import threading
import time
from urllib.parse import quote

import paramiko

logger = logging.getLogger(__name__)

_SSH_TIMEOUT = 30
_CMD_TIMEOUT = 600

# SSH keepalive interval: keeps NAT/firewall mappings alive during the
# long steps and detects dead connections fast.
_KEEPALIVE_S = 30

# Background step polling: how often to check the .rc file / progress,
# and the hard cap before giving up on a detached process.
_BG_POLL_S = 60
_BG_MAX_WAIT_S = 24 * 3600

_SCRATCH_MOUNT = "/mnt/scratch"

# Max presigned URL lifetime (OBS allows up to 7 days; stay under it).
_PRESIGN_TTL = 6 * 86400


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
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(_KEEPALIVE_S)
            return client
        except Exception as e:
            logger.info("  SSH not ready yet (%s), retrying...", e)
            time.sleep(5)
    raise RuntimeError(f"Could not SSH to {public_ip} after 120s")


class _SshSession:
    """SSH connection that survives drops: reconnects on demand.

    The pipeline runs for hours over the public internet; any NAT/proxy/
    Wi-Fi blip kills the TCP connection. Every command asks the session
    for a live client and transparently gets a fresh connection when the
    old transport is no longer active. Long-running work is decoupled
    from the session entirely (see _start_bg/_wait_bg), so reconnects
    never interrupt remote progress.
    """

    def __init__(self, public_ip: str, private_key_pem: str) -> None:
        self._public_ip = public_ip
        self._pem = private_key_pem
        self._client: paramiko.SSHClient | None = None

    def client(self, force: bool = False) -> paramiko.SSHClient:
        if not force and self._client is not None:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return self._client
            logger.warning("Sesión SSH caída — reconectando a %s ...", self._public_ip)
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._client = _ssh_connect(self._public_ip, self._pem)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


def _obs_presign(
    ak: str, sk: str, verb: str, bucket: str, key: str, host: str, expires: int
) -> str:
    """Presigned URL using the OBS V2 (S3-compatible) query signature.

    StringToSign = VERB\\nContent-MD5\\nContent-Type\\nExpires\\nResource
    With no Content-Type/MD5 and no x-obs headers this collapses to
    VERB, two empty lines, the expiry and /bucket/key. The request must
    not send a Content-Type (curl strips it with -H 'Content-Type:').
    """
    resource = f"/{bucket}/{key}"
    string_to_sign = f"{verb}\n\n\n{expires}\n{resource}"
    digest = hmac.new(
        sk.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    return (
        f"https://{host}/{quote(key)}"
        f"?AccessKeyId={quote(ak, safe='')}"
        f"&Expires={expires}"
        f"&Signature={quote(signature, safe='')}"
    )


def _tail(text: str, n: int = 15) -> str:
    """Last n non-empty lines of an output (for error messages)."""
    lines = [l for l in text.replace("\r", "\n").split("\n") if l.strip()]
    return "\n".join(lines[-n:]) if lines else "(sin salida)"


def _as_text(raw) -> str:
    """Normalize paramiko readline output (bytes in binary mode, str in text mode)."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def _run_cmd(
    ssh: "_SshSession | paramiko.SSHClient",
    cmd: str,
    timeout: int = _CMD_TIMEOUT,
    display: str | None = None,
    retry_on_drop: bool = True,
) -> str:
    """Run a command over SSH, streaming output to the log. Returns stdout.

    - `ssh` may be a raw client or a _SshSession (auto-reconnecting).
    - `display` overrides what gets logged (used to redact presigned URLs).
    - `retry_on_drop`: retry ONCE when the connection drops mid-command
      (channel closed without exit status / dead transport). Safe for
      read-only commands; disabled for side-effect commands (mkfs/mount,
      background launches) where a blind retry could apply twice.
    - stderr is drained in a thread (a full buffer deadlocks the command)
      and both streams are logged sampled (first 10 lines + every 50th)
      so chatty commands don't flood the log.
    - The channel timeout is per-read idle, NOT total duration: commands
      that keep producing output can run for hours.
    - On failure the error includes the tail of BOTH streams.
    """
    logger.info("  $ %s", display or cmd)
    attempts = 2 if (retry_on_drop and isinstance(ssh, _SshSession)) else 1
    drop_reason = ""
    for attempt in range(1, attempts + 1):
        if isinstance(ssh, _SshSession):
            client = ssh.client(force=(attempt > 1))
        else:
            client = ssh
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        except paramiko.SSHException as e:
            if attempt < attempts:
                logger.warning("  Sesión SSH inactiva (%s) — reconectando...", e)
                continue
            raise

        err_buf: list[str] = []

        def _drain_stderr() -> None:
            try:
                while True:
                    raw = stderr.readline()
                    if not raw:
                        break
                    line = _as_text(raw)
                    err_buf.append(line)
                    n = len(err_buf)
                    if n <= 10 or n % 50 == 0:
                        logger.info("    [stderr] %s", line.rstrip())
            except Exception:
                pass

        err_thread = threading.Thread(target=_drain_stderr, daemon=True)
        err_thread.start()

        out_buf: list[str] = []
        dropped = False
        try:
            while True:
                raw = stdout.readline()
                if not raw:
                    break
                line = _as_text(raw)
                out_buf.append(line)
                n = len(out_buf)
                if n <= 10 or n % 50 == 0:
                    logger.info("    %s", line.rstrip())
        except socket.timeout:
            raise RuntimeError(
                f"Sin output del comando por {timeout}s (timeout del canal SSH): {display or cmd}"
            )
        except OSError as e:
            # Transport died mid-read (connection reset / closed socket).
            dropped = True
            drop_reason = str(e)

        exit_code = -1
        if not dropped:
            # -1 = channel closed WITHOUT an exit status: the transport
            # died mid-command (network drop), not a real command failure.
            exit_code = stdout.channel.recv_exit_status()
            if exit_code == -1:
                dropped = True
                drop_reason = "canal cerrado sin exit status"
        err_thread.join(timeout=10)

        if dropped:
            if attempt < attempts:
                logger.warning(
                    "  Conexión SSH cayó a mitad del comando (%s) — reconectando y reintentando...",
                    drop_reason,
                )
                continue
            raise RuntimeError(
                f"Conexión SSH cayó a mitad del comando ({drop_reason}): {display or cmd}"
            )

        out = "".join(out_buf)
        err = "".join(err_buf)
        if len(out_buf) > 10:
            logger.info("    ... (%d lineas de stdout, ultimas 5:)", len(out_buf))
            for line in out_buf[-5:]:
                logger.info("    %s", line.rstrip())

        if exit_code != 0:
            raise RuntimeError(
                f"Command failed (exit {exit_code}): {display or cmd}\n"
                f"--- stdout (tail) ---\n{_tail(out)}\n"
                f"--- stderr (tail) ---\n{_tail(err)}"
            )
        return out
    raise RuntimeError(f"Comando no ejecutado tras reintento: {display or cmd}")


def _try_cmd(ssh: _SshSession, cmd: str, timeout: int) -> bool:
    """Run a command, returning True on success, False (logged) on failure."""
    try:
        _run_cmd(ssh, cmd, timeout=timeout)
        return True
    except RuntimeError as e:
        logger.warning("  Paso fallo — probando siguiente alternativa:\n%s", e)
        return False


def _cmd_ok(ssh: _SshSession, cmd: str, timeout: int) -> bool:
    """Quiet success check: True on exit 0, False otherwise (no warning).

    For presence checks where failure is an expected outcome, not an
    error: _try_cmd's "Paso fallo — probando siguiente alternativa" reads
    like a skipped install alternative when it only means 'not installed'.
    """
    try:
        _run_cmd(ssh, cmd, timeout=timeout)
        return True
    except RuntimeError:
        return False


def _log_qemu_img_version(ssh: _SshSession) -> None:
    """Best-effort version logging via the package manager.

    Old qemu-img (1.5.3 on CentOS 7) has no --version flag: it dumps its
    usage to stdout and exits 1, so the package query is the only reliable
    way to log which build is in use.
    """
    try:
        _run_cmd(
            ssh,
            "rpm -q qemu-img 2>/dev/null || dpkg -s qemu-utils 2>/dev/null | grep -i ^Version",
            timeout=30,
        )
    except RuntimeError:
        pass


def _install_qemu_img(ssh: _SshSession) -> None:
    """Ensure qemu-img is available on the helper ECS.

    The Huawei CentOS image ships qemu-img already; presence is checked
    with 'command -v' because 'qemu-img --version' is NOT a valid check
    on old builds (usage dump + exit 1).

    Install order if absent: stock yum repos -> deterministic
    vault.centos.org repo file (CentOS 7 EOL fallback) -> qemu-kvm
    (pulls qemu-img as a dependency) -> apt.
    """
    logger.info("  Diagnostico del OS de la ECS auxiliar:")
    _run_cmd(
        ssh,
        "head -2 /etc/os-release 2>/dev/null; ls /etc/yum.repos.d/ 2>/dev/null | head -10",
        timeout=30,
    )

    if _cmd_ok(ssh, "command -v qemu-img", timeout=30):
        _log_qemu_img_version(ssh)
        return
    logger.info(
        "  qemu-img no está preinstalado en esta imagen — instalando "
        "(orden de alternativas: repos originales -> vault.centos.org -> "
        "qemu-kvm -> apt)"
    )

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

    if not _cmd_ok(ssh, "command -v qemu-img", timeout=30):
        raise RuntimeError(
            "qemu-img no quedo instalado tras agotar todas las alternativas "
            "(revisar la salida de yum/apt arriba en el log)"
        )
    _log_qemu_img_version(ssh)


def _list_disks(ssh: _SshSession) -> list[tuple[str, int]]:
    """Disks in attach order: [(name, size_bytes), ...]."""
    out = _run_cmd(ssh, 'lsblk -b -d -o NAME,SIZE,TYPE | awk \'$3=="disk"{print $1, $2}\'')
    disks: list[tuple[str, int]] = []
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            disks.append((parts[0], int(parts[1])))
    return disks


def _wait_for_disks(
    ssh: _SshSession,
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
        # EVS 'size_gb' values are GiB (1100 -> exactly 1100 * 2^30 bytes).
        data_gib = disks[1][1] / 2**30
        if abs(data_gib - expected_data_gb) > expected_data_gb * 0.05:
            raise RuntimeError(
                f"El segundo disco mide {data_gib:.0f}GiB pero se esperaban "
                f"~{expected_data_gb}GiB (orden de attach inesperado): {disks}"
            )
    roles = {"root": disks[0], "data": disks[1]}
    if len(disks) >= 3:
        roles["scratch"] = disks[2]
    logger.info("Discos: root=%s data=%s scratch=%s",
                disks[0], disks[1], roles.get("scratch"))
    return roles


def _pick_obs_host(
    ssh: _SshSession,
    bucket: str,
    region: str,
    expected_data_gb: int | None,
    max_public_gib: int = 100,
) -> str:
    """Choose the OBS upload host, preferring the internal endpoint.

    The internal endpoint (obs.<region>.internal.myhuaweicloud.com) keeps
    traffic intra-region: no EIP bandwidth limit. It only resolves via the
    region's private DNS, so resolution is tested on the ECS itself with
    getent (same glibc resolver curl uses) and /etc/resolv.conf is logged
    for diagnosis. Falls back to the public endpoint only for small disks
    (a big VHD through the 5 Mbps EIP would take weeks).
    """
    internal = f"{bucket}.obs.{region}.internal.myhuaweicloud.com"
    public = f"{bucket}.obs.{region}.myhuaweicloud.com"

    logger.info("  DNS de la ECS (resolv.conf):")
    _run_cmd(ssh, "cat /etc/resolv.conf", timeout=15)

    if _try_cmd(ssh, f"getent hosts {internal}", timeout=30):
        logger.info("  Endpoint interno OK: %s", internal)
        return internal
    logger.warning("  Endpoint interno %s NO resuelve desde la ECS", internal)

    if _try_cmd(ssh, f"getent hosts {public}", timeout=30):
        if expected_data_gb and expected_data_gb > max_public_gib:
            raise RuntimeError(
                f"Solo el endpoint PUBLICO de OBS resuelve, pero el disco es de "
                f"{expected_data_gb}GiB: por el EIP de 5Mbps la subida tomaria "
                "semanas. El subnet necesita el DNS privado de la region en "
                "primary_dns/secondary_dns (revisar el resolv.conf del log)."
            )
        logger.warning(
            "  Usando endpoint PUBLICO de OBS (%s) — subida limitada al "
            "bandwidth del EIP", public,
        )
        return public

    raise RuntimeError(
        "Ni el endpoint interno ni el publico de OBS resuelven desde la ECS: "
        "el subnet no tiene DNS funcional. Revisar primary_dns/secondary_dns "
        "del subnet y el resolv.conf del log."
    )


def _start_bg(
    ssh: _SshSession, cmd: str, log_path: str, display: str | None = None
) -> str:
    """Launch `cmd` DETACHED on the ECS so it survives SSH drops.

    Output is redirected to log_path and the exit code written to
    log_path + '.rc' by the wrapping bash. nohup + redirects + `< /dev/null`
    detach the process from the SSH channel: closing the connection (or
    our laptop's Wi-Fi dying) does not kill it. Returns the remote PID.

    The launcher itself must NOT be retried on a connection drop (a blind
    retry could launch the job twice) — hence retry_on_drop=False.
    """
    rc_path = f"{log_path}.rc"
    inner = f"{cmd}; echo $? > {shlex.quote(rc_path)}"
    launcher = (
        f"rm -f {shlex.quote(rc_path)}; "
        f"nohup bash -c {shlex.quote(inner)} "
        f"> {shlex.quote(log_path)} 2>&1 < /dev/null & echo BG_PID=$!"
    )
    out = _run_cmd(ssh, launcher, timeout=60, display=display, retry_on_drop=False)
    for line in out.splitlines():
        if line.startswith("BG_PID="):
            pid = line.split("=", 1)[1].strip()
            logger.info("  Background iniciado (PID %s), progreso en %s", pid, log_path)
            return pid
    raise RuntimeError(f"No se obtuvo el PID del proceso background: {out!r}")


def _wait_bg(
    ssh: _SshSession,
    pid: str,
    log_path: str,
    display: str,
    poll_s: int = _BG_POLL_S,
    max_wait_s: int = _BG_MAX_WAIT_S,
) -> None:
    """Poll a detached process until its .rc file appears.

    Each poll is a short command through the auto-reconnecting session:
    an SSH drop only interrupts the MONITORING, never the remote work.
    Progress (last line of the log, CR-normalized) is logged as a
    heartbeat on every change.
    """
    rc_path = f"{log_path}.rc"
    deadline = time.time() + max_wait_s
    last_heartbeat = ""
    while True:
        out = _run_cmd(
            ssh,
            f"cat {shlex.quote(rc_path)} 2>/dev/null; echo ---RC-END---; "
            f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD; "
            f"echo ---TAIL---; tail -c 400 {shlex.quote(log_path)} 2>/dev/null "
            f"| tr '\\r' '\\n' | tail -n 2",
            timeout=90,
            display=f"poll {display}",
        )
        head, _, rest = out.partition("---RC-END---")
        rc = head.strip()
        alive_txt, _, tail_txt = rest.partition("---TAIL---")
        heartbeat = " ".join(tail_txt.split())
        if heartbeat and heartbeat != last_heartbeat:
            last_heartbeat = heartbeat
            logger.info("    [%s] %s", display, heartbeat)
        if rc:
            code = int(rc) if rc.lstrip("-").isdigit() else -1
            if code != 0:
                detail = _run_cmd(
                    ssh,
                    f"tail -c 4000 {shlex.quote(log_path)} 2>/dev/null "
                    f"| tr '\\r' '\\n' | tail -n 25",
                    timeout=90,
                )
                raise RuntimeError(
                    f"Paso background '{display}' fallo (exit {code}):\n{_tail(detail, 20)}"
                )
            logger.info("  %s: completado (exit 0)", display)
            return
        if "DEAD" in alive_txt:
            raise RuntimeError(
                f"El proceso de '{display}' (PID {pid}) murio sin dejar exit code "
                f"(reboot de la ECS o kill -9). Ultimo output: {heartbeat or '(none)'}"
            )
        if time.time() > deadline:
            raise RuntimeError(
                f"'{display}' no termino en {max_wait_s // 3600}h (PID {pid} sigue "
                f"vivo en la ECS); el proceso NO fue detenido — revisar {log_path}"
            )
        time.sleep(poll_s)


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

    The bucket must live in `region` (the ECS's region) so the internal
    endpoint applies. Steps:
    1. SSH connect, ensure qemu-img
    2. Pick the OBS host (internal preferred, DNS diagnostics logged)
    3. mkfs + mount scratch disk (vdc)
    4. HEAD pre-flight against the chosen endpoint (fast fail)
    5. qemu-img convert -f raw -O vpc /dev/vdb <scratch>/export.vhd
    6. curl PUT via presigned URL + HEAD verification

    Steps 5 and 6 run DETACHED on the ECS (nohup) and are monitored by
    polling: they survive SSH drops, and the session reconnects on its
    own for everything else.
    """
    ssh = _SshSession(public_ip, private_key_pem)
    try:
        logger.info("[4b/6] Verificando qemu-img en la ECS...")
        _install_qemu_img(ssh)

        host = _pick_obs_host(ssh, bucket_name, region, expected_data_gb)
        expires = int(time.time()) + _PRESIGN_TTL
        put_url = _obs_presign(ak, sk, "PUT", bucket_name, object_key, host, expires)
        head_url = _obs_presign(ak, sk, "HEAD", bucket_name, object_key, host, expires)
        redacted = f"https://{host}/{quote(object_key)}?<firma-oculta>"

        roles = _wait_for_disks(ssh, expected_data_gb=expected_data_gb)
        data_dev = f"/dev/{roles['data'][0]}"
        scratch_dev = f"/dev/{roles['scratch'][0]}"

        logger.info("[5b/6] Preparando disco scratch %s (mkfs + mount)...", scratch_dev)
        _run_cmd(
            ssh,
            f"mkfs.ext4 -F {scratch_dev} && mkdir -p {_SCRATCH_MOUNT} "
            f"&& mount {scratch_dev} {_SCRATCH_MOUNT}",
            timeout=600,
            retry_on_drop=False,
        )
        _run_cmd(ssh, f"df -h {_SCRATCH_MOUNT}", timeout=30)

        logger.info("[5b/6] Pre-flight del upload contra OBS interno...")
        code_out = _run_cmd(
            ssh,
            f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Content-Type:' '{head_url}'",
            timeout=120,
            display=f"curl HEAD {redacted}",
        )
        code = code_out.strip().splitlines()[-1].strip() if code_out.strip() else ""
        if code not in ("200", "404"):
            raise RuntimeError(
                f"Pre-flight OBS devolvio HTTP {code!r} (esperaba 404 = objeto "
                "aun inexistente, o 200 = ya existe de una corrida previa). "
                "Revisar: DNS del endpoint interno, firma, o bucket en otra region."
            )
        logger.info("Pre-flight OK (HTTP %s) — endpoint interno, TLS y firma validos", code)

        vhd_path = f"{_SCRATCH_MOUNT}/export.vhd"
        logger.info("[5b/6] Convirtiendo %s a VHD (background — puede tardar horas)...", data_dev)
        # 'vpc' is qemu's canonical name for the VHD format; old qemu-img
        # (1.5.3) does not recognize the 'vhd' alias. Detached + polled so
        # an SSH drop never kills a multi-hour convert.
        pid = _start_bg(
            ssh,
            f"qemu-img convert -p -f raw -O vpc {data_dev} {vhd_path}",
            "/tmp/convert.log",
            display="qemu-img convert -p -f raw -O vpc (a scratch)",
        )
        _wait_bg(ssh, pid, "/tmp/convert.log", "convert")

        size_out = _run_cmd(ssh, f"ls -lh {vhd_path} | awk '{{print $5}}'", timeout=30)
        logger.info("VHD file size: %s", size_out.strip())

        logger.info("[6b/6] Subiendo VHD a OBS via endpoint interno (curl PUT, background)...")
        pid = _start_bg(
            ssh,
            f"curl -f --retry 3 -H 'Content-Type:' -T {vhd_path} '{put_url}'",
            "/tmp/upload.log",
            display=f"curl -T {vhd_path} {redacted}",
        )
        _wait_bg(ssh, pid, "/tmp/upload.log", "upload")

        logger.info("[6b/6] Verificando objeto en OBS (HEAD)...")
        head_out = _run_cmd(
            ssh,
            f"curl -sI -H 'Content-Type:' '{head_url}'",
            timeout=120,
            display=f"curl -sI {redacted}",
        )
        status_line = head_out.strip().splitlines()[0] if head_out.strip() else ""
        if " 200" not in status_line:
            raise RuntimeError(f"Verificacion HEAD fallo: {status_line!r} (esperaba 200)")
        for line in head_out.splitlines():
            if line.lower().startswith("content-length"):
                logger.info("Objeto en OBS — %s", line.strip())

        _run_cmd(ssh, f"rm -f {vhd_path}", timeout=60)

        logger.info("Export directo completado: obs://%s/%s", bucket_name, object_key)
        return True

    finally:
        ssh.close()
