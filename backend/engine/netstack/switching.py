"""L2 switching: MAC learning, 802.1Q VLANs and RSTP (802.1w) port roles.

The switch floods unknown/broadcast frames within a VLAN, learns source MACs
per VLAN, and runs a compact spanning-tree implementation (root election via
BPDUs, root/designated/alternate/backup port roles) so redundant L2
topologies converge instead of melting down in a broadcast storm.

Port roles (IEEE 802.1w / 802.1D-2004 §17): every non-root bridge has one
root port (best path to root) and zero-or-more designated ports (one per
segment it wins). A port that loses the comparison discards instead — which
of the two discarding roles it gets is decided by *who* sent the superior
BPDU it lost to: the same neighbour bridge already used by the root port
(a second link to a bridge we already reach) is a **backup** port; a
different neighbour bridge (a path to root via someone else entirely) is an
**alternate** port. See ``_recompute_roles`` for the comparison.

Port state transitions (802.1D §8.4, unchanged by the RSTP role split): a
port moving onto the forwarding track (root or designated) passes through
Listening (no forwarding, no MAC learning) then Learning (MAC learning,
still no forwarding) before Forwarding, each phase lasting one Forward
Delay. A port moving to discarding (alternate/backup/blocked) does so
immediately — the whole point is to stop forwarding fast enough to avoid a
loop. Two shortcuts around that slow path (RSTP-b, §17.3):

- **Proposal/Agreement rapid transition** on point-to-point links: a
  designated port not yet forwarding sets ``proposal`` on its BPDUs. A
  bridge whose port becomes root port on receiving a superior proposal
  first *syncs* (forces its own other non-edge designated ports back to
  discarding, so nothing can loop) then jumps that root port straight to
  forwarding and replies with ``agreement``. The proposer, on seeing that
  agreement, jumps its own designated port to forwarding too — no Forward
  Delay wait on either side. If the agreement never arrives, the normal
  Listening/Learning chain (already armed in parallel) still completes it.
- **Edge ports**: a port that has never received a BPDU is assumed to have
  no bridge on the other end and goes straight to forwarding. Receiving a
  BPDU permanently revokes edge status and the port follows the normal
  role/state machine (including the handshake above) from then on.

Simplifications vs. real 802.1w (documented, deliberate):
- topology-change notifications (TC/TCA) are not modelled;
- BPDU max-age pruning uses the same dead-interval mechanism as hellos;
- no BPDU guard / root guard, no MSTP / per-VLAN STP;
- every link is treated as point-to-point (no half-duplex/shared-media
  detection — there's no hub device in this engine anyway).
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
# 802.1D §8.4 Forward Delay: derived from STP_HELLO (one Hello Time per
# Listening/Learning phase) rather than a new hardcoded constant, so a
# freshly-designated port's convergence stays proportional to this module's
# own BPDU cadence instead of the (much larger) textbook 15s default.
STP_FORWARD_DELAY = STP_HELLO


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
        # Per-port sequence-guard for the Listening->Learning->Forwarding
        # chain (same pattern as bgp.py's _arm_hold / vrrp.py's _timer_seq):
        # bumped on every (re)arm so a superseded generation's delayed
        # callback recognizes itself as stale and no-ops instead of
        # advancing a state that has since moved on (re-election, or
        # re-blocked) mid-delay.
        self._delay_seq: dict[str, int] = {}
        # Sticky per-port "has this port ever received a BPDU" marker
        # (RSTP-b edge-port detection, §17.3): absence means edge — no
        # bridge has ever been heard on the other end, so the port can
        # jump straight to forwarding. Receiving one BPDU revokes it for
        # good (matches real hardware: only an admin/link-flap reset would
        # bring it back, which this engine doesn't need to model).
        self._not_edge: set[str] = set()

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
            self._send_bpdu(
                net,
                iface,
                idx,
                root_prio,
                root_mac,
                cost,
                # RSTP-b: propose while not yet forwarding — this is how a
                # peer learns it's safe to sync+agree instead of waiting
                # out Forward Delay. Stops on its own once forwarding.
                proposal=iface.stp_state != "forwarding",
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
    def _port_index(self, iface: Interface) -> int:
        return list(self.interfaces).index(iface.name)

    def _send_bpdu(
        self,
        net: Network,
        iface: Interface,
        idx: int,
        root_prio: int,
        root_mac: str,
        cost: int,
        *,
        proposal: bool = False,
        agreement: bool = False,
    ) -> None:
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
                    port_role=iface.stp_role,
                    learning=iface.stp_state in ("learning", "forwarding"),
                    forwarding=iface.stp_state == "forwarding",
                    proposal=proposal,
                    agreement=agreement,
                ),
            ),
        )

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

        # Reference bridge for the alternate/backup split below: the
        # neighbour bridge our root port already reaches (or our own bridge
        # mac if we have no root port — i.e. we're root, where this never
        # actually gets used since we win on every port).
        root_pb = self._port_best.get(root_port) if root_port else None
        root_bridge_mac = root_pb.bridge_mac if root_pb is not None else self.bridge_mac

        my_prio, my_mac = self.priority, self.bridge_mac
        for name, iface in self.interfaces.items():
            if not self.stp_enabled:
                iface.stp_role, iface.stp_state = "designated", "forwarding"
                continue
            if name == root_port:
                self._enter_forwarding_track(net, iface, "root")
                continue
            pb = self._port_best.get(name)
            if pb is None:
                # Nothing better heard: we are designated on this segment.
                self._enter_forwarding_track(net, iface, "designated")
                continue
            # Compare our offer on this segment vs. the best heard on it.
            _, _, my_cost = self._current_root(net)
            ours = (root_prio, root_mac, my_cost, my_prio, my_mac)
            theirs = (pb.root_prio, pb.root_mac, pb.cost, pb.bridge_prio, pb.bridge_mac)
            if ours < theirs:
                self._enter_forwarding_track(net, iface, "designated")
            else:
                # Lost the comparison: discarding, but which flavour depends
                # on whether the bridge that beat us here is the *same*
                # neighbour our root port already goes through (a second
                # link to a bridge we already reach -> backup) or some
                # other bridge entirely (a genuinely different path to
                # root -> alternate).
                role = "backup" if pb.bridge_mac == root_bridge_mac else "alternate"
                self._enter_blocking(iface, role)

    def _enter_blocking(self, iface: Interface, role: str = "blocked") -> None:
        """To-blocking is always immediate (§8.4) — a port must stop
        forwarding fast enough to prevent a loop, never wait out a delay.
        ``role`` defaults to the generic "blocked" tag for callers (tests)
        that just want *a* discarding state without classifying it; real
        role selection always comes from ``_recompute_roles`` and passes
        "alternate" or "backup" explicitly."""
        if iface.stp_role == role and iface.stp_state == "blocking":
            return  # already there; nothing in flight to abort
        iface.stp_role, iface.stp_state = role, "blocking"
        # Force-abort: invalidate any in-flight Listening/Learning chain for
        # this port so its delayed callback recognizes itself as stale.
        self._delay_seq[iface.name] = self._delay_seq.get(iface.name, 0) + 1

    def _enter_forwarding_track(self, net: Network, iface: Interface, role: str) -> None:
        """Root/designated: Listening -> Learning -> Forwarding, one Forward
        Delay per phase. A no-op if this port is already on this exact
        track (avoids restarting the clock on every periodic recompute)."""
        if iface.stp_role == role and iface.stp_state in ("listening", "learning", "forwarding"):
            return
        iface.stp_role = role
        self._delay_seq[iface.name] = self._delay_seq.get(iface.name, 0) + 1
        if iface.name not in self._not_edge:
            # Edge port (RSTP-b §17.3): nobody has ever sent a BPDU here,
            # so there's no bridge on the other end to sync/negotiate
            # with — go straight to Forwarding, no Listening/Learning.
            iface.stp_state = "forwarding"
            return
        iface.stp_state = "listening"
        seq = self._delay_seq[iface.name]
        net.scheduler.schedule_after(
            STP_FORWARD_DELAY,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._enter_learning(net, iface, seq),
                node_id=self.node_id,
            ),
        )

    def _enter_learning(self, net: Network, iface: Interface, seq: int) -> None:
        if self._delay_seq.get(iface.name) != seq:
            return  # superseded — port moved on (re-blocked, re-armed) already
        iface.stp_state = "learning"
        net.scheduler.schedule_after(
            STP_FORWARD_DELAY,
            SimEvent(
                time=0.0,
                type=EventType.TIMER,
                handler=lambda _c, _e: self._enter_forwarding(net, iface, seq),
                node_id=self.node_id,
            ),
        )

    def _enter_forwarding(self, net: Network, iface: Interface, seq: int) -> None:
        if self._delay_seq.get(iface.name) != seq:
            return
        iface.stp_state = "forwarding"

    def _handle_bpdu(self, net: Network, iface: Interface, bpdu: BpduFrame) -> None:
        if not self.stp_enabled:
            return
        self._not_edge.add(iface.name)  # a bridge exists on the other end
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

        # RSTP-b rapid transition (§17.3), point-to-point links only (this
        # engine models none other). Two independent triggers:
        if bpdu.proposal and iface.stp_role == "root" and iface.stp_state != "forwarding":
            # This port just became our root port on a superior proposal:
            # sync, jump to forwarding, tell the proposer it's safe too.
            self._sync_and_agree(net, iface)
        if bpdu.agreement and iface.stp_role == "designated" and iface.stp_state != "forwarding":
            # Our proposal was agreed to: skip straight to forwarding
            # instead of waiting out the Listening/Learning chain already
            # armed by _recompute_roles above.
            self._delay_seq[iface.name] = self._delay_seq.get(iface.name, 0) + 1
            iface.stp_state = "forwarding"

    def _sync_and_agree(self, net: Network, root_port: Interface) -> None:
        """Force our other non-edge designated ports to discarding first
        (no loop window while the new root port jumps ahead), then move
        ``root_port`` straight to Forwarding and reply with agreement."""
        for name, other in self.interfaces.items():
            if other is root_port:
                continue
            if other.stp_role == "designated" and name in self._not_edge:
                self._enter_blocking(other, "designated")

        self._delay_seq[root_port.name] = self._delay_seq.get(root_port.name, 0) + 1
        root_port.stp_state = "forwarding"
        root_prio, root_mac, cost = self._current_root(net)
        self._send_bpdu(
            net,
            root_port,
            self._port_index(root_port),
            root_prio,
            root_mac,
            cost,
            agreement=True,
        )

        # Let the just-blocked ports start reconverging immediately instead
        # of waiting up to one Hello Time for the next periodic recompute.
        self._recompute_roles(net)

    # ----- data plane -----------------------------------------------------------------
    def on_frame(self, net: Network, iface: Interface, frame: EthernetFrame) -> None:
        if not self.powered_on:
            return

        if isinstance(frame.payload, BpduFrame):
            self._handle_bpdu(net, iface, frame.payload)
            return

        if iface.stp_state in ("blocking", "listening"):
            # §8.4: neither state participates in the data plane at all —
            # Listening doesn't even learn MACs yet.
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

        # Learn source MAC — Learning does this, just doesn't forward yet.
        src = str(frame.src_mac)
        if not MacAddr(src).is_multicast:
            self.mac_table[(vlan, src)] = iface.name

        if iface.stp_state == "learning":
            net.record_drop("stp_blocked")
            return

        # Forward.
        dst = str(frame.dst_mac)
        if not frame.is_broadcast and not MacAddr(dst).is_multicast:
            out_name = self.mac_table.get((vlan, dst))
            if out_name is not None and out_name != iface.name:
                out = self.interfaces.get(out_name)
                if out is not None and out.stp_state == "forwarding" and out.vlan_allows(vlan):
                    self._egress(net, out, frame, vlan)
                return
            if out_name == iface.name:
                return  # destination is back where it came from; filter

        # Flood: broadcast, multicast, or unknown unicast. LAG members are
        # skipped — their logical port floods once for the whole bundle.
        for name, out in self.interfaces.items():
            if name == iface.name or out.lag_parent is not None or not out.is_up:
                continue
            if out.stp_state != "forwarding" or not out.vlan_allows(vlan):
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
