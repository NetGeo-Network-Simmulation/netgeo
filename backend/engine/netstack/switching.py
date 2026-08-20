"""L2 switching: MAC learning, 802.1Q VLANs and simplified 802.1D STP.

The switch floods unknown/broadcast frames within a VLAN, learns source MACs
per VLAN, and runs a compact spanning-tree implementation (root election via
configuration BPDUs, root/designated/blocked port roles) so redundant L2
topologies converge instead of melting down in a broadcast storm.

Simplifications vs. real 802.1D (documented, deliberate):
- no listening/learning transition delays (ports jump to their final state);
- topology-change notifications are not modelled;
- BPDU max-age pruning uses the same dead-interval mechanism as hellos.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.events import EventType, SimEvent
from engine.netstack.addr import STP_MULTICAST_MAC, MacAddr
from engine.netstack.device import Device
from engine.netstack.frames import BpduFrame, EthernetFrame
from engine.netstack.iface import Interface

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

STP_HELLO = 2.0
STP_MAX_AGE = 20.0


@dataclass(slots=True)
class _PortBpdu:
    """Best BPDU heard on a port + when we heard it."""

    root_prio: int
    root_mac: str
    cost: int
    bridge_prio: int
    bridge_mac: str
    port_id: int
    heard_at: float

    def vector(self) -> tuple:
        return (self.root_prio, self.root_mac, self.cost, self.bridge_prio, self.bridge_mac, self.port_id)


def _parse_bridge_id(bid: str) -> tuple[int, str]:
    prio, mac = bid.split(".", 1)
    return int(prio), mac


class Switch(Device):
    """A learning switch with VLANs and STP."""

    kind = "switch"

    def __init__(
        self,
        name: str,
        node_id: str | None = None,
        nos: str = "forgeos",
        stp_enabled: bool = True,
        priority: int = 32768,
    ) -> None:
        super().__init__(name, node_id, nos)
        self.stp_enabled = stp_enabled
        self.priority = priority
        # (vlan, mac) -> iface name
        self.mac_table: dict[tuple[int, str], str] = {}
        self._port_best: dict[str, _PortBpdu] = {}
        self._started = False

    # ----- identity ----------------------------------------------------------
    @property
    def bridge_mac(self) -> str:
        macs = sorted(str(i.mac) for i in self.interfaces.values())
        return macs[0] if macs else "00:00:00:00:00:00"

    @property
    def bridge_id(self) -> str:
        return f"{self.priority}.{self.bridge_mac}"

    def _my_vector(self) -> tuple:
        """This bridge's claim as (root_prio, root_mac, cost, prio, mac)."""
        return (self.priority, self.bridge_mac, 0, self.priority, self.bridge_mac, 0)

    # ----- lifecycle ------------------------------------------------------------
    def start(self, net: Network) -> None:
        """Kick off periodic STP hellos (idempotent)."""
        if self._started or not self.stp_enabled:
            return
        self._started = True
        self._hello(net)

    def _hello(self, net: Network) -> None:
        if not self.powered_on:
            return
        self._age_out(net)
        self._recompute_roles(net)
        root_prio, root_mac, cost = self._current_root(net)
        for idx, iface in enumerate(self.interfaces.values()):
            if not iface.is_up or iface.lag_parent is not None:
                continue
            # Only designated ports originate BPDUs (root port listens).
            if iface.stp_role != "designated":
                continue
            iface.transmit(
                net,
                EthernetFrame(
                    src_mac=iface.mac,
                    dst_mac=STP_MULTICAST_MAC,
                    ethertype=0x0027,
                    payload=BpduFrame(
                        root_id=f"{root_prio}.{root_mac}",
                        root_cost=cost,
                        bridge_id=self.bridge_id,
                        port_id=idx,
                    ),
                ),
            )
        net.scheduler.schedule_after(
            STP_HELLO,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._hello(net),
                node_id=self.node_id,
            ),
        )

    # ----- STP machinery ------------------------------------------------------------
    def _current_root(self, net: Network) -> tuple[int, str, int]:
        """(root_prio, root_mac, my_cost_to_root)."""
        best = (self.priority, self.bridge_mac, 0)
        for pb in self._port_best.values():
            candidate = (pb.root_prio, pb.root_mac, pb.cost + 1)
            if candidate[:2] < best[:2] or (candidate[:2] == best[:2] and candidate[2] < best[2]):
                best = candidate
        return best

    def _age_out(self, net: Network) -> None:
        cutoff = net.now - STP_MAX_AGE
        for port, pb in list(self._port_best.items()):
            if pb.heard_at < cutoff:
                del self._port_best[port]

    def _recompute_roles(self, net: Network) -> None:
        root_prio, root_mac, _ = self._current_root(net)
        i_am_root = (root_prio, root_mac) == (self.priority, self.bridge_mac)

        # Root port: the port with the best received offer toward the root.
        root_port: str | None = None
        if not i_am_root:
            best_vec: tuple | None = None
            for name, pb in self._port_best.items():
                if (pb.root_prio, pb.root_mac) != (root_prio, root_mac):
                    continue
                vec = pb.vector()
                if best_vec is None or vec < best_vec:
                    best_vec = vec
                    root_port = name

        my_prio, my_mac = self.priority, self.bridge_mac
        for name, iface in self.interfaces.items():
            if not self.stp_enabled:
                iface.stp_role, iface.stp_state = "designated", "forwarding"
                continue
            if name == root_port:
                iface.stp_role, iface.stp_state = "root", "forwarding"
                continue
            pb = self._port_best.get(name)
            if pb is None:
                # Nothing better heard: we are designated on this segment.
                iface.stp_role, iface.stp_state = "designated", "forwarding"
                continue
            # Compare our offer on this segment vs. the best heard on it.
            _, _, my_cost = self._current_root(net)
            ours = (root_prio, root_mac, my_cost, my_prio, my_mac)
            theirs = (pb.root_prio, pb.root_mac, pb.cost, pb.bridge_prio, pb.bridge_mac)
            if ours < theirs:
                iface.stp_role, iface.stp_state = "designated", "forwarding"
            else:
                iface.stp_role, iface.stp_state = "blocked", "blocking"

    def _handle_bpdu(self, net: Network, iface: Interface, bpdu: BpduFrame) -> None:
        if not self.stp_enabled:
            return
        root_prio, root_mac = _parse_bridge_id(bpdu.root_id)
        bprio, bmac = _parse_bridge_id(bpdu.bridge_id)
        incoming = _PortBpdu(
            root_prio=root_prio,
            root_mac=root_mac,
            cost=bpdu.root_cost,
            bridge_prio=bprio,
            bridge_mac=bmac,
            port_id=bpdu.port_id,
            heard_at=net.now,
        )
        current = self._port_best.get(iface.name)
        if current is None or incoming.vector() <= current.vector():
            self._port_best[iface.name] = incoming
        self._recompute_roles(net)

    # ----- data plane -----------------------------------------------------------------
    def on_frame(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        if not self.powered_on:
            return

        if isinstance(frame.payload, BpduFrame):
            self._handle_bpdu(net, iface, frame.payload)
            return

        if iface.stp_state == "blocking":
            net.record_drop("stp_blocked")
            return

        # Classify VLAN.
        if iface.vlan_mode == "access":
            if frame.vlan is not None and frame.vlan != iface.access_vlan:
                net.record_drop("vlan_mismatch")
                return
            vlan = iface.access_vlan
        else:  # trunk
            vlan = frame.vlan if frame.vlan is not None else 1
            if not iface.vlan_allows(vlan):
                net.record_drop("vlan_filtered")
                return

        # Learn source MAC.
        src = str(frame.src_mac)
        if not MacAddr(src).is_multicast:
            self.mac_table[(vlan, src)] = iface.name

        # Forward.
        dst = str(frame.dst_mac)
        if not frame.is_broadcast and not MacAddr(dst).is_multicast:
            out_name = self.mac_table.get((vlan, dst))
            if out_name is not None and out_name != iface.name:
                out = self.interfaces.get(out_name)
                if out is not None and out.stp_state != "blocking" and out.vlan_allows(vlan):
                    self._egress(net, out, frame, vlan)
                return
            if out_name == iface.name:
                return  # destination is back where it came from; filter

        # Flood: broadcast, multicast, or unknown unicast. LAG members are
        # skipped — their logical port floods once for the whole bundle.
        for name, out in self.interfaces.items():
            if name == iface.name or out.lag_parent is not None or not out.is_up:
                continue
            if out.stp_state == "blocking" or not out.vlan_allows(vlan):
                continue
            self._egress(net, out, frame.clone(), vlan)

    @staticmethod
    def _egress(net: Network, out: Interface, frame: EthernetFrame, vlan: int) -> None:
        # Tag on trunks, strip on access ports.
        frame.vlan = vlan if out.vlan_mode == "trunk" else None
        out.transmit(net, frame)

    # ----- introspection ---------------------------------------------------------------
    def mac_table_rows(self) -> list[dict]:
        return [
            {"vlan": vlan, "mac": mac, "port": port}
            for (vlan, mac), port in sorted(self.mac_table.items())
        ]
