"""First concrete :class:`EmulationAdaptor`: spawns real NOS containers via
the rootless Podman REST socket (``podman-py``).

One Podman network per link uses explicit static IPs. FRR configuration and
single-command console operations are scoped to labeled, leased containers.
The legacy PTY ``attach_console`` hook remains unsupported.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import socket as _socket
import tarfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable

from podman import PodmanClient
from podman.errors import APIError, NotFound

from engine.emulation.adaptor import (
    EmulatedNodeHandle,
    EmulationAdaptor,
    EmulationStatus,
)
from engine.emulation.ip_alloc import link_subnet
from engine.emulation.kinds import FRR_CAPTURE_IMAGE, KINDS
from engine.model import InterfaceModel, LinkModel
from engine.netstack.device import Device

SOCKET_PATH = f"/run/user/{os.getuid()}/podman/podman.sock" if hasattr(os, "getuid") else ""
CONTAINER_PREFIX = "netgeo-"
LINK_NETWORK_PREFIX = "netgeo-link-"
logger = logging.getLogger(__name__)


def socket_reachable(path: str = SOCKET_PATH) -> bool:
    """True if the rootless Podman REST socket exists and accepts a connection."""
    if not path or not os.path.exists(path):
        return False
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    try:
        sock.connect(path)
        return True
    except OSError:
        return False
    finally:
        sock.close()


class PodmanSocketUnreachable(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"podman REST socket unreachable at {SOCKET_PATH} — enable it with: "
            "systemctl --user enable --now podman.socket"
        )


class UnknownKind(RuntimeError):
    pass


class ImageNotLocal(RuntimeError):
    """Raised for EULA-gated kinds whose image the user must fetch themselves."""

    def __init__(self, kind: str, image: str) -> None:
        super().__init__(
            f"kind {kind!r} needs image {image!r}, which is not present locally. "
            f"NetGeo never pulls EULA-gated images — download it yourself per the "
            f"vendor's license (see docs/byoi/{kind}.md), then `podman pull {image}`."
        )


def _parse_busybox_ping(output: str, sent: int) -> dict:
    """Parse Alpine/busybox ``ping -c`` output (the FRR image's shell) into
    the shape ``PingReport.as_dict()`` uses for sim-mode pings."""
    rtts = [round(float(m), 3) for m in re.findall(r"time=([\d.]+)\s*ms", output)]
    m = re.search(r"(\d+) packets transmitted, (\d+) packets received", output)
    received = int(m.group(2)) if m else len(rtts)
    return {
        "sent": sent,
        "received": received,
        "loss_pct": round(100.0 * (sent - received) / sent, 1) if sent else 0.0,
        "rtts_ms": rtts,
        "min_ms": min(rtts) if rtts else None,
        "avg_ms": round(sum(rtts) / len(rtts), 3) if rtts else None,
        "max_ms": max(rtts) if rtts else None,
        "errors": [] if received else ["no reply"],
    }


def _config_tree(text: str) -> tuple[set[tuple[str, ...]], dict[tuple[str, ...], list[str]]]:
    """Keep FRR stanza ancestry and command order; ignore display metadata."""
    scopes: set[tuple[str, ...]] = set()
    commands: dict[tuple[str, ...], list[str]] = defaultdict(list)
    stack: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if (not line or line.startswith(("!", "%", "Configuration file[", "frr version ", "frr defaults "))
                or line in {"Building configuration...", "Current configuration:", "end",
                            "exit-vrf", "exit-address-family"}):
            continue
        indent = len(raw) - len(raw.lstrip())
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = tuple(name for _, name in stack)
        if line.startswith(("interface ", "router ", "vrf ", "address-family ")) or line == "line vty":
            scope = (*parent, line)
            if line != "line vty":  # an empty line-vty stanza is omitted by FRR
                scopes.add(scope)
            stack.append((indent, line))
        else:
            commands[parent].append(line)
    return scopes, commands


def _running_contains(config: str, running: str) -> bool:
    wanted_scopes, wanted = _config_tree(config)
    actual_scopes, actual = _config_tree(running)
    if not wanted_scopes and not any(wanted.values()):
        return False
    if not wanted_scopes.issubset(actual_scopes):
        return False
    for scope, requested in wanted.items():
        observed = actual.get(scope, [])
        counts = Counter(requested)
        seen = Counter(observed)
        if any(seen[line] != count for line, count in counts.items()):
            return False
        remaining = iter(observed)
        if not all(any(line == candidate for candidate in remaining) for line in requested):
            return False
    return True


class PodmanAdaptor(EmulationAdaptor):
    """Spawns/destroys containers over the rootless Podman REST socket."""

    name = "podman"

    def __init__(
        self, project_id: str | None = None, lab_id: str | None = None,
        lease_guard: Callable[[], bool] | None = None,
    ) -> None:
        self._client = None  # lazy: only import/connect podman on first use
        self._lease_guard = lease_guard
        self._labels = {"netgeo.managed": "true"}
        self.capture_enabled = False
        if project_id:
            self._labels["netgeo.project"] = project_id
        if lab_id:
            self._labels["netgeo.lab"] = lab_id

    def _get_client(self):
        if self._client is None:
            if not socket_reachable():
                raise PodmanSocketUnreachable
            self._client = PodmanClient(base_url=f"unix://{SOCKET_PATH}")
        return self._client

    def use_local_capture_image(self) -> bool:
        """Choose the opt-in local image before spawning this lab's nodes."""
        self.capture_enabled = self._get_client().images.exists(FRR_CAPTURE_IMAGE)
        return self.capture_enabled

    async def spawn(self, device: Device) -> EmulatedNodeHandle:
        kind = device.nos
        spec = KINDS.get(kind)
        if spec is None:
            raise UnknownKind(f"unregistered emulation kind {kind!r}")

        client = self._get_client()
        image = FRR_CAPTURE_IMAGE if kind == "frr" and self.capture_enabled else spec.image
        if not client.images.exists(image):
            if image == FRR_CAPTURE_IMAGE:
                raise RuntimeError("local FRR capture image disappeared before lab startup")
            if not spec.bundlable:
                raise ImageNotLocal(kind, spec.image)
            client.images.pull(spec.image)

        name = f"{CONTAINER_PREFIX}{device.node_id}"
        container = client.containers.create(
            image,
            name=name,
            cap_add=spec.caps,
            mem_limit=f"{spec.mem_limit_mb}m",
            labels={**self._labels, "netgeo.kind": kind},
            hostname=device.name,
            detach=True,
            # bridge, not the rootless default (pasta) — pasta-mode containers
            # can't join the extra per-link networks wire_link() attaches (N-3).
            network_mode="bridge",
        )
        container.start()
        container.reload()
        return EmulatedNodeHandle(
            node_id=device.node_id,
            container_id=container.id,
            status=EmulationStatus.RUNNING
            if container.status == "running"
            else EmulationStatus.PROVISIONING,
            meta={"name": name, "kind": kind},
        )

    async def destroy(self, node_id: str) -> None:
        client = self._get_client()
        try:
            container = client.containers.get(f"{CONTAINER_PREFIX}{node_id}")
        except NotFound:
            return  # already gone — idempotent
        try:
            container.stop()
        except (NotFound, APIError):
            pass
        try:
            container.remove(force=True)
        except NotFound:
            pass

    def _owned_container(self, node_id: str, client=None):
        if not self._labels.get("netgeo.project") or not self._labels.get("netgeo.lab"):
            raise PermissionError("a project lab is required for console/config access")
        if self._lease_guard is None or not self._lease_guard():
            raise PermissionError("NetGeo emulation lease is required")
        container = (client or self._get_client()).containers.get(f"{CONTAINER_PREFIX}{node_id}")
        container.reload()
        expected = {**self._labels, "netgeo.kind": "frr"}
        if any((container.labels or {}).get(k) != v for k, v in expected.items()):
            raise PermissionError("container ownership mismatch")
        if container.status != "running":
            raise RuntimeError("FRR container is not running")
        return container

    @staticmethod
    def _exec(container, argv: list[str]) -> tuple[int, str]:
        code, streams = container.exec_run(argv, demux=True)
        stdout, stderr = streams if isinstance(streams, tuple) else (streams, None)
        return code, ((stdout or b"") + (stderr or b"")).decode(errors="replace")

    @classmethod
    def _restart_ready(cls, container) -> None:
        # Restart resets daemon state, making the file a replacement instead
        # of vtysh -b's additive merge. Podman keeps container networks.
        container.restart()
        for _ in range(20):
            code, daemons = cls._exec(container, ["vtysh", "-c", "show daemons"])
            if code == 0 and {"mgmtd", "zebra", "staticd"}.issubset(daemons.split()):
                return
            time.sleep(0.5)
        raise RuntimeError("FRR daemons did not become ready after restart")

    @classmethod
    def _apply_file(cls, container) -> tuple[int, str]:
        cls._restart_ready(container)
        return cls._exec(container, ["vtysh", "-b"])

    async def push_config(self, node_id: str, config: str, fmt: str = "cli") -> dict:
        return await asyncio.to_thread(self._push_config, node_id, config, fmt)

    def _push_config(self, node_id: str, config: str, fmt: str = "cli") -> dict:
        if fmt != "cli":
            raise ValueError("FRR supports CLI configuration only")
        if not config.strip() or "\x00" in config or len(config.encode()) > 1024 * 1024:
            raise ValueError("FRR configuration must be nonempty UTF-8 text under 1 MiB")
        container = self._owned_container(node_id)
        before_code, before = self._exec(container, ["vtysh", "-c", "show running-config"])
        if before_code:
            raise RuntimeError("FRR running state unavailable before apply")
        temporary = f".netgeo-{uuid.uuid4().hex}.conf"
        backup = f"{temporary}.backup"
        target = "/etc/frr/frr.conf"
        staged = f"/etc/frr/{temporary}"
        saved = f"/etc/frr/{backup}"
        moved = False
        applied = False
        result = {"applied": True, "verified": True}
        keep_backup = False
        had_original = self._exec(container, ["test", "-f", target])[0] == 0
        vtysh_config = "/etc/frr/vtysh.conf"
        had_vtysh = self._exec(container, ["test", "-f", vtysh_config])[0] == 0
        try:
            with io.BytesIO() as archive:
                with tarfile.open(fileobj=archive, mode="w") as tar:
                    payload = config.encode()
                    info = tarfile.TarInfo(temporary)
                    info.size = len(payload)
                    info.mode = 0o600
                    tar.addfile(info, io.BytesIO(payload))
                if not container.put_archive("/etc/frr", archive.getvalue()):
                    raise RuntimeError("FRR configuration upload failed")
            if not had_vtysh and self._exec(container, ["touch", vtysh_config])[0]:
                raise RuntimeError("FRR vtysh initialization failed")
            operations = (["cp", "-p", target, saved], ["mv", staged, target]) if had_original else (["mv", staged, target],)
            operations += (["chown", "frr:frr", target], ["chmod", "640", target])
            for argv in operations:
                code, _ = self._exec(container, argv)
                if code:
                    raise RuntimeError("FRR configuration staging failed")
                if argv[0] == "mv":
                    moved = True
            code, _ = self._apply_file(container)
            if code:
                raise RuntimeError("FRR rejected configuration")
            code, running = self._exec(container, ["vtysh", "-c", "show running-config"])
            if code:
                raise RuntimeError("FRR running configuration verification failed")
            if not _running_contains(config, running):
                raise RuntimeError("FRR running configuration differs from artifact")
            applied = True
            return result
        except Exception as exc:
            if moved:
                try:
                    code, _ = self._exec(container, ["mv", saved, target] if had_original else ["rm", "-f", target])
                except Exception as rollback_exc:  # noqa: BLE001 - Podman can fail with transport errors
                    keep_backup = had_original
                    logger.error("FRR rollback failed for node=%s; backup at %s; cause=%s",
                                 node_id, saved, type(rollback_exc).__name__)
                    raise RuntimeError(
                        f"FRR apply failed and file rollback failed ({type(rollback_exc).__name__})"
                    ) from exc
                if code:
                    keep_backup = had_original
                    if keep_backup:
                        logger.error("FRR rollback failed for node=%s; backup preserved at %s", node_id, saved)
                    raise RuntimeError("FRR apply failed and file rollback failed") from exc
                # Fresh daemons restore the previous running configuration.
                try:
                    if had_original:
                        rollback_code, _ = self._apply_file(container)
                        if rollback_code:
                            raise RuntimeError("rollback reload returned a nonzero status")
                    else:
                        self._restart_ready(container)
                    code, restored = self._exec(container, ["vtysh", "-c", "show running-config"])
                    if code or not _running_contains(before, restored):
                        raise RuntimeError("previous running state was not restored")
                except Exception as rollback_exc:  # noqa: BLE001 - include Podman transport failures
                    logger.error("FRR rollback reload failed for node=%s: %s", node_id, type(rollback_exc).__name__)
                    raise RuntimeError(
                        f"FRR apply failed and rollback reload failed ({type(rollback_exc).__name__})"
                    ) from exc
            raise
        finally:
            cleanup_failed = False
            try:
                cleanup_code, _ = self._exec(container, ["rm", "-f", staged] if keep_backup else ["rm", "-f", staged, saved])
                cleanup_failed = cleanup_code != 0
                if cleanup_failed:
                    logger.warning("FRR temporary file cleanup returned %s for node=%s", cleanup_code, node_id)
            except Exception as cleanup_exc:  # noqa: BLE001 - cleanup must not mask apply/rollback
                cleanup_failed = True
                logger.warning("FRR temporary file cleanup failed for node=%s: %s", node_id, type(cleanup_exc).__name__)
            if not had_vtysh and not applied:
                try:
                    cleanup_code, _ = self._exec(container, ["rm", "-f", vtysh_config])
                    cleanup_failed |= cleanup_code != 0
                    if cleanup_code:
                        logger.warning("FRR vtysh cleanup returned %s for node=%s", cleanup_code, node_id)
                except Exception as cleanup_exc:  # noqa: BLE001 - cleanup must not mask apply/rollback
                    cleanup_failed = True
                    logger.warning("FRR vtysh cleanup failed for node=%s: %s", node_id, type(cleanup_exc).__name__)
            if cleanup_failed and applied:
                result["cleanup_verified"] = False

    async def command(self, node_id: str, command: str) -> str:
        return await asyncio.to_thread(self._command, node_id, command)

    def _command(self, node_id: str, command: str) -> str:
        if not command.strip() or len(command) > 4096 or any(c in command for c in "\r\n\x00"):
            raise ValueError("one nonempty console command per request is required")
        container = self._owned_container(node_id)
        code, output = self._exec(container, ["vtysh", "-c", command])
        if code:
            raise RuntimeError("FRR console command failed")
        return output

    async def wire_link(self, link: LinkModel, a: InterfaceModel, b: InterfaceModel) -> None:
        client = self._get_client()
        cidr, gateway, ip_a, ip_b = link_subnet(link.id)
        network = client.networks.create(
            f"{LINK_NETWORK_PREFIX}{link.id}",
            driver="bridge",
            labels={**self._labels, "netgeo.link": link.id},
            ipam={"Config": [{"Subnet": cidr, "Gateway": gateway}]},
        )
        container_a = client.containers.get(f"{CONTAINER_PREFIX}{a.node_id}")
        container_b = client.containers.get(f"{CONTAINER_PREFIX}{b.node_id}")
        network.connect(container_a, ipv4_address=ip_a)
        network.connect(container_b, ipv4_address=ip_b)

    async def destroy_link(self, link_id: str) -> None:
        client = self._get_client()
        network_name = f"{LINK_NETWORK_PREFIX}{link_id}"
        try:
            network = client.networks.get(network_name)
        except NotFound:
            return
        # The network's own "containers" field (libpod inspect) is not
        # reliably populated on every podman version/timing (see
        # test_link_e2e.py's CI regression) — ask each container directly
        # instead, via its own NetworkSettings.Networks, which is stable.
        for container in client.containers.list(all=True, sparse=False):
            if network_name in ((container.attrs.get("NetworkSettings") or {}).get("Networks") or {}):
                try:
                    network.disconnect(container, force=True)
                except (NotFound, APIError):
                    pass
        # Do not swallow a genuine removal failure (e.g. still in use because
        # a disconnect above silently failed) — that hid this exact bug.
        try:
            network.remove()
        except NotFound:
            pass

    async def status(self, node_id: str) -> EmulationStatus:
        client = self._get_client()
        try:
            container = client.containers.get(f"{CONTAINER_PREFIX}{node_id}")
        except NotFound:
            return EmulationStatus.ABSENT
        container.reload()
        return EmulationStatus.RUNNING if container.status == "running" else EmulationStatus.STOPPED

    async def ping(self, node_id: str, dest_ip: str, count: int = 4) -> dict:
        client = self._get_client()
        try:
            container = client.containers.get(f"{CONTAINER_PREFIX}{node_id}")
        except NotFound:
            raise NotFound(f"no running container for node {node_id!r}") from None
        # demux=True: exec_run's combined-stream bytes are docker/podman's
        # multiplexed frame format (8-byte binary header per chunk) — decoding
        # that as UTF-8 text corrupts/crashes on the header bytes. demux gives
        # back already-deframed (stdout, stderr).
        _exit_code, (stdout, _stderr) = container.exec_run(
            ["ping", "-c", str(count), "-W", "2", dest_ip], demux=True
        )
        text = (stdout or b"").decode(errors="replace")
        return _parse_busybox_ping(text, count)

    async def link_address(self, node_id: str, link_id: str) -> str:
        """Read the address Podman actually assigned to this link endpoint."""
        def read() -> str:
            container = self._owned_container(node_id)
            container.reload()
            network = ((container.attrs.get("NetworkSettings") or {}).get("Networks") or {}).get(
                f"{LINK_NETWORK_PREFIX}{link_id}", {}
            )
            address = network.get("IPAddress")
            if not address:
                raise RuntimeError("emulation link address unavailable")
            return address

        return await asyncio.to_thread(read)

    def capture_interface(self, node_id: str, link_id: str) -> str:
        """Resolve a stored link to the actual interface in its owned container."""
        container = self._owned_container(node_id)
        container.reload()
        network = ((container.attrs.get("NetworkSettings") or {}).get("Networks") or {}).get(
            f"{LINK_NETWORK_PREFIX}{link_id}", {}
        )
        address = network.get("IPAddress")
        if not address:
            raise ValueError("interface is not attached to this node and link")
        network = self._get_client().networks.get(f"{LINK_NETWORK_PREFIX}{link_id}")
        labels = (network.attrs or {}).get("labels") or (network.attrs or {}).get("Labels") or {}
        if any(labels.get(k) != v for k, v in {**self._labels, "netgeo.link": link_id}.items()):
            raise PermissionError("link ownership mismatch")
        code, output = self._exec(container, ["ip", "-4", "addr", "show"])
        if code:
            raise RuntimeError("container interface discovery failed")
        current = None
        for line in output.splitlines():
            match = re.match(r"^\d+: ([^:@]+)(?:@[^:]+)?:", line)
            if match:
                current = match.group(1)
            if current and re.search(rf"\binet {re.escape(address)}/\d+\b", line):
                if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", current):
                    break
                return current
        raise RuntimeError("container interface for link address unavailable")

    def capture_pcap(self, node_id: str, interface: str, path: str, duration: int) -> bytes:
        with PodmanClient(base_url=f"unix://{SOCKET_PATH}", timeout=duration + 5) as client:
            container = self._owned_container(node_id, client)
            try:
                # No user-supplied command, filter, path, or interface reaches exec.
                code, _ = self._exec(container, [
                    "timeout", "-s", "INT", str(duration), "tcpdump", "-i", interface,
                    "-s", "160", "-c", "256", "-U", "-w", path,
                ])
                if code not in (0, 124):
                    raise RuntimeError("tcpdump capture failed")
                code, data = container.exec_run(["head", "-c", "65537", path], demux=True)
                payload = data[0] if isinstance(data, tuple) else data
                if code or not isinstance(payload, bytes) or len(payload) > 65536 or payload[:4] not in (
                    b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4",
                ):
                    raise RuntimeError("bounded PCAP result unavailable")
                return payload
            finally:
                try:
                    self._exec(container, ["rm", "-f", path])
                except Exception:
                    logger.warning("capture file cleanup failed for node=%s", node_id)

    def stop_capture(self, node_id: str, path: str) -> None:
        """Signal only the tcpdump process for this internal capture path."""
        with PodmanClient(base_url=f"unix://{SOCKET_PATH}", timeout=5) as client:
            container = self._owned_container(node_id, client)
            code, output = self._exec(container, ["ps", "-o", "pid,args"])
            if code:
                raise RuntimeError("capture process inspection failed")
            for line in output.splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[0].isdigit() and "tcpdump" in fields[1].split() and path in fields[1].split():
                    self._exec(container, ["kill", "-INT", fields[0]])
            self._exec(container, ["rm", "-f", path])

    async def attach_console(self, node_id: str):
        raise NotImplementedError("attach_console lands with ws.py wiring")
