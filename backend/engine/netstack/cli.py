"""Per-device CLI emulation over live netstack state.

Two dialects, chosen from the device's NOS:

- **Cisco-like** (ios/iosxr/nxos/eos/frr/vyos/forgeos/...): exec + privileged
  + config modes, ``show`` commands rendered from the actual tables, and a
  working ``ping``/``traceroute`` that runs the simulator.
- **MikroTik-like** (routeros): ``/ip address print`` style.

A :class:`CliSession` is stateful (mode, selected interface) so the console
window behaves like a real terminal session.
"""
from __future__ import annotations

from ipaddress import IPv4Interface, IPv4Network, ip_address
from typing import TYPE_CHECKING

from engine.netstack.addr import parse_ip_interface6
from engine.netstack.device import Device, Host
from engine.netstack.routing import Router
from engine.netstack.switching import Switch

if TYPE_CHECKING:  # pragma: no cover
    from engine.netstack.network import Network

MIKROTIK_NOS = {"routeros"}

_SOURCE_CODE = {
    "connected": "C",
    "static": "S",
    "ospf": "O",
    "isis": "i",
    "ebgp": "B",
    "ibgp": "B",
    "rip": "R",
    "vpnv4": "V",
}


class CliSession:
    """One interactive console session bound to a device."""

    def __init__(self, net: Network, device: Device) -> None:
        self.net = net
        self.device = device
        self.mode = "exec"            # exec | priv | config | config-if
        self.current_iface: str | None = None
        self.dialect = "mikrotik" if device.nos in MIKROTIK_NOS else "cisco"

    # ----- prompt ---------------------------------------------------------
    @property
    def prompt(self) -> str:
        name = self.device.name
        if self.dialect == "mikrotik":
            return f"[admin@{name}] > "
        if self.mode == "exec":
            return f"{name}>"
        if self.mode == "priv":
            return f"{name}#"
        if self.mode == "config":
            return f"{name}(config)#"
        return f"{name}(config-if)#"

    # ----- entry point ---------------------------------------------------------
    def execute(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        try:
            if self.dialect == "mikrotik":
                return self._mikrotik(line)
            return self._cisco(line)
        except Exception as exc:  # noqa: BLE001 - never crash the console REPL
            return f"% error: {exc}\n"

    # =========================== Cisco-like ==================================
    def _cisco(self, line: str) -> str:
        low = line.lower()
        words = low.split()

        # Mode navigation first.
        if low in ("?", "help"):
            return self._cisco_help()
        if low == "enable":
            self.mode = "priv" if self.mode == "exec" else self.mode
            return ""
        if low in ("disable",):
            self.mode = "exec"
            return ""
        if low in ("configure terminal", "conf t", "configure"):
            if self.mode == "exec":
                return "% Command authorization failed: use 'enable' first\n"
            self.mode = "config"
            return ""
        if low == "end":
            if self.mode in ("config", "config-if"):
                self.mode = "priv"
                self.current_iface = None
            return ""
        if low == "exit":
            if self.mode == "config-if":
                self.mode, self.current_iface = "config", None
            elif self.mode == "config":
                self.mode = "priv"
            elif self.mode == "priv":
                self.mode = "exec"
            return ""

        if self.mode in ("config", "config-if"):
            return self._cisco_config(line, words)

        # Exec/priv commands.
        if words[0] == "show":
            return self._cisco_show(low)
        if words[0] == "ping" and len(words) >= 2:
            return self._do_ping(words[1], count=int(words[2]) if len(words) > 2 else 4)
        if words[0] in ("traceroute", "tracert") and len(words) >= 2:
            return self._do_traceroute(words[1])
        return f"% Invalid input: {line}\n"

    def _cisco_config(self, line: str, words: list[str]) -> str:
        dev = self.device
        if words[0] == "interface" and len(words) >= 2:
            name = line.split(None, 1)[1]
            if name not in dev.interfaces:
                return f"% Interface {name} not found\n"
            self.current_iface = name
            self.mode = "config-if"
            return ""
        if self.mode == "config-if" and self.current_iface:
            iface = dev.interfaces[self.current_iface]
            if words[0] == "ip" and len(words) >= 3 and words[1] == "address":
                try:
                    if len(words) == 4:  # ip address A.B.C.D M.M.M.M
                        iface.ips = [IPv4Interface(f"{words[2]}/{words[3]}")]
                    else:                # ip address A.B.C.D/nn
                        iface.ips = [IPv4Interface(words[2])]
                except ValueError as exc:
                    return f"% {exc}\n"
                if isinstance(dev, Router):
                    dev.sync_connected_routes()
                return ""
            if words[0] == "ipv6" and len(words) == 3 and words[1] == "address":
                try:
                    iface.ips6 = [parse_ip_interface6(words[2])]
                except ValueError as exc:
                    return f"% {exc}\n"
                if isinstance(dev, Router):
                    dev.sync_connected_routes()
                return ""
            if words[0] == "shutdown":
                iface.enabled = False
                return ""
            if line.lower() == "no shutdown":
                iface.enabled = True
                return ""
            if words[0] == "switchport" and len(words) >= 3:
                if words[1] == "mode":
                    iface.vlan_mode = words[2]
                    return ""
                if words[1] == "access" and words[2] == "vlan" and len(words) == 4:
                    iface.access_vlan = int(words[3])
                    return ""
        if words[0] == "ip" and len(words) >= 4 and words[1] == "route":
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            try:
                if len(words) == 5:  # ip route NET MASK NEXTHOP
                    prefix = IPv4Network(f"{words[2]}/{words[3]}")
                    dev.add_static_route(prefix, words[4])
                else:                # ip route NET/nn NEXTHOP
                    dev.add_static_route(words[2], words[3])
            except ValueError as exc:
                return f"% {exc}\n"
            return ""
        if words[0] == "ipv6" and len(words) >= 4 and words[1] == "route":
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            try:               # ipv6 route PREFIX/nn NEXTHOP [IFACE]
                dev.add_static_route6(
                    words[2], words[3], iface_name=words[4] if len(words) > 4 else None
                )
            except ValueError as exc:
                return f"% {exc}\n"
            return ""
        if line.lower() == "ipv6 nd ra enable":
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            dev.enable_ra()
            return ""
        return f"% Invalid config command: {line}\n"

    # ----- show family ------------------------------------------------------------
    def _cisco_show(self, low: str) -> str:
        dev = self.device
        if low.startswith("show version"):
            return (
                f"NetGeo ForgeOS Software, {dev.kind.upper()} platform "
                f"(nos={dev.nos})\nDevice {dev.name}, uptime {self.net.now:.1f}s sim-time\n"
            )
        if low.startswith(("show ip interface brief", "show ip int br")):
            rows = ["Interface        IP-Address         OK? Status"]
            for i in dev.interfaces.values():
                ip = str(i.ips[0]) if i.ips else "unassigned"
                status = "up" if i.is_up else ("admin-down" if not i.enabled else "down")
                rows.append(f"{i.name:<16} {ip:<18} YES {status}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show interfaces", "show interface")):
            out = []
            for i in dev.interfaces.values():
                c = i.counters
                out.append(
                    f"{i.name} is {'up' if i.is_up else 'down'}, address {i.mac}\n"
                    f"  ips: {', '.join(str(x) for x in i.ips) or '-'}\n"
                    f"  rx {c.rx_frames} frames/{c.rx_bytes} bytes, "
                    f"tx {c.tx_frames} frames/{c.tx_bytes} bytes\n"
                    f"  drops: queue={c.drops_queue} loss={c.drops_loss} "
                    f"mtu={c.drops_mtu} down={c.drops_down}"
                )
            return "\n".join(out) + "\n"
        if low.startswith("show ipv6 route"):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            rows = ["IPv6 Routing Table - Codes: C - connected, S - static\n"]
            for r in dev.route_table_rows6():
                code = _SOURCE_CODE.get(r["source"], "?")
                via = f"via {r['next_hop']}" if r["next_hop"] else "directly connected"
                dev_part = f", {r['iface']}" if r["iface"] else ""
                rows.append(f"{code}    {r['prefix']} [{r['ad']}/{r['metric']}] {via}{dev_part}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show ipv6 neighbors", "show ipv6 neighbor")):
            if not isinstance(dev, (Host, Router)):
                return "% No neighbor table on this device\n"
            rows = ["IPv6 Address                             Link-layer Addr      Interface"]
            for ip, (mac, ifname) in sorted(dev.nd_cache.items(), key=lambda kv: str(kv[0])):
                rows.append(f"{ip!s:<40} {mac:<20} {ifname}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show ipv6 interface brief", "show ipv6 int br")):
            rows = ["Interface        Status  IPv6 Address(es)"]
            for i in dev.interfaces.values():
                status = "up" if i.is_up else ("admin-down" if not i.enabled else "down")
                addrs = [str(i.link_local)] + [str(x) for x in i.ips6]
                rows.append(f"{i.name:<16} {status:<7} {', '.join(addrs)}")
            return "\n".join(rows) + "\n"
        if low.startswith("show ip route vrf"):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            name = low.split("show ip route vrf", 1)[1].strip()
            if name not in dev.vrfs:
                return f"% VRF {name} not configured\n"
            rows = [f"Routing Table: VRF {name}",
                    "Codes: C - connected, V - VPNv4 (MP-BGP)\n"]
            for r in dev.vrf_route_rows(name):
                code = _SOURCE_CODE.get(r["source"], "?")
                via = f"via {r['next_hop']}" if r["next_hop"] else "directly connected"
                dev_part = f", {r['iface']}" if r["iface"] else ""
                lbl = f" label {r['vpn_label']}" if r.get("vpn_label") is not None else ""
                rows.append(f"{code}    {r['prefix']} [{r['ad']}/{r['metric']}] {via}{dev_part}{lbl}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show mpls forwarding-table", "show mpls forwarding")):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            rows = ["Local  Outgoing   Prefix              Next Hop         Interface"]
            for e in dev.mpls_forwarding_rows():
                out_label = str(e["out_label"]) if e["action"] == "swap" else "Pop Label"
                rows.append(
                    f"{e['local_label']:<6} {out_label:<10} {e['prefix']:<19} "
                    f"{(e['next_hop'] or '-'):<16} {e['out_iface'] or '-'}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith(("show segment-routing sid-database", "show sr sid-database")):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            sr = self._proc("sr")
            if sr is None:
                return "% Segment routing not enabled\n"
            rows = ["Router ID        Prefix              SID    Label"]
            for r in sr.sid_rows():
                rows.append(f"{r['router_id']:<16} {r['prefix']:<19} {r['sid']:<6} {r['label']}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show segment-routing adjacency-sid", "show sr adjacency-sid")):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            rows = ["Label   Peer             Interface"]
            for r in dev.sr_adj_rows():
                rows.append(f"{r['label']:<7} {r['peer']:<16} {r['out_iface']}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show bgp evpn", "show bgp l2vpn evpn")):
            vx = self._proc("vxlan")
            if vx is None:
                return "% EVPN/VXLAN is not enabled\n"
            rows = ["Type  VNI     MAC                  VTEP             Origin"]
            for r in vx.evpn_rows():
                rows.append(
                    f"T{r['type']:<4} {r['vni']:<7} {r['mac']:<20} "
                    f"{r['vtep']:<16} {r['origin']}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith(("show vxlan vtep", "show nve peers")):
            vx = self._proc("vxlan")
            if vx is None:
                return "% EVPN/VXLAN is not enabled\n"
            rows = ["VNI     Remote VTEP"]
            for r in vx.vtep_rows():
                rows.append(f"{r['vni']:<7} {r['remote_vtep']}")
            return "\n".join(rows) + "\n"
        if low.startswith("show ip route"):
            if not isinstance(dev, Router):
                return "% This device does not route\n"
            rows = ["Codes: C - connected, S - static, O - OSPF, i - IS-IS, B - BGP\n"]
            for r in dev.route_table_rows():
                code = _SOURCE_CODE.get(r["source"], "?")
                via = f"via {r['next_hop']}" if r["next_hop"] else "directly connected"
                dev_part = f", {r['iface']}" if r["iface"] else ""
                rows.append(f"{code}    {r['prefix']} [{r['ad']}/{r['metric']}] {via}{dev_part}")
            return "\n".join(rows) + "\n"
        if low.startswith("show arp"):
            if not isinstance(dev, (Host, Router)):
                return "% No ARP table on this device\n"
            rows = ["Address          Hardware Addr        Interface"]
            for ip, (mac, ifname) in sorted(dev.arp_table.items()):
                rows.append(f"{ip!s:<16} {mac:<20} {ifname}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show mac address-table", "show mac-address-table")):
            if not isinstance(dev, Switch):
                return "% Not a switch\n"
            rows = ["Vlan  Mac Address         Port"]
            for row in dev.mac_table_rows():
                rows.append(f"{row['vlan']:<5} {row['mac']:<19} {row['port']}")
            return "\n".join(rows) + "\n"
        if low.startswith("show vlan"):
            if not isinstance(dev, Switch):
                return "% Not a switch\n"
            rows = ["Port     Mode    VLAN(s)"]
            for i in dev.interfaces.values():
                vlans = (
                    str(i.access_vlan)
                    if i.vlan_mode == "access"
                    else (
                        ",".join(map(str, sorted(i.trunk_vlans)))
                        if i.trunk_vlans
                        else "all"
                    )
                )
                rows.append(f"{i.name:<8} {i.vlan_mode:<7} {vlans}")
            return "\n".join(rows) + "\n"
        if low.startswith("show spanning-tree"):
            if not isinstance(dev, Switch):
                return "% Not a switch\n"
            rows = [f"Bridge ID {dev.bridge_id} (STP {'on' if dev.stp_enabled else 'off'})",
                    "Port     Role        State"]
            for i in dev.interfaces.values():
                rows.append(f"{i.name:<8} {i.stp_role:<11} {i.stp_state}")
            return "\n".join(rows) + "\n"
        if low.startswith("show ip ospf neighbor"):
            proc = self._proc("ospf")
            if proc is None:
                return "% OSPF is not running\n"
            rows = ["Neighbor ID     State   Area  Address          Interface"]
            for n in proc.neighbor_rows():
                rows.append(
                    f"{n['router_id']:<15} {n['state']:<7} {n.get('area', 0):<5} "
                    f"{n['ip']:<16} {n['iface']}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith(("show isis neighbors", "show clns neighbors")):
            proc = self._proc("isis")
            if proc is None:
                return "% IS-IS is not running\n"
            rows = ["System Id       Interface  State  IP Address"]
            for n in proc.neighbor_rows():
                rows.append(
                    f"{n['system_id']:<15} {n['iface']:<10} {n['state']:<6} {n['ip']}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith("show isis database"):
            proc = self._proc("isis")
            if proc is None:
                return "% IS-IS is not running\n"
            rows = ["LSPID                 Seq  Links"]
            for l in proc.lsdb_rows():
                rows.append(f"{l['system_id']:<21} {l['seq']:<4} {l['links']}")
            return "\n".join(rows) + "\n"
        if low.startswith(("show ip bgp summary", "show bgp summary")):
            proc = self._proc("bgp")
            if proc is None:
                return "% BGP is not running\n"
            rows = [f"BGP router identifier {proc.router_id}, local AS {proc.asn}",
                    "Neighbor         AS      State         PfxRcd"]
            for p in proc.summary_rows():
                rows.append(
                    f"{p['neighbor']:<16} {p['remote_as']:<7} {p['state']:<13} {p['prefixes_received']}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith(("show etherchannel", "show lacp")):
            from engine.netstack.lag import LagInterface

            lags = [i for i in dev.interfaces.values() if isinstance(i, LagInterface)]
            if not lags:
                return "% No port-channels configured\n"
            rows = ["Group  Mode    Ports"]
            for lag in lags:
                r = lag.status_row(self.net.now)
                ports = " ".join(
                    f"{m['name']}({'P' if m['bundled'] else 'D' if m['up'] else 's'})"
                    for m in r["members"]
                )
                rows.append(f"{r['name']:<6} {r['mode']:<7} {ports}")
            rows.append("(P) bundled  (D) up, not bundled  (s) link down")
            return "\n".join(rows) + "\n"
        if low.startswith(("show vrrp", "show standby")):
            procs = [
                p for p in getattr(dev, "processes", [])
                if getattr(p, "proto", "") == "vrrp"
            ]
            if not procs:
                return "% VRRP is not running\n"
            # ios-like operators expect HSRP's "standby" vocabulary.
            title = "Standby" if low.startswith("show standby") else "VRRP"
            rows = [f"{title} brief",
                    "Interface  Grp  Prio  State    Virtual IP       Virtual MAC"]
            for p in procs:
                r = p.status_row()
                rows.append(
                    f"{r['iface']:<10} {r['vrid']:<4} {r['priority']:<5} "
                    f"{r['state']:<8} {r['vip']:<16} {r['vmac']}"
                )
            return "\n".join(rows) + "\n"
        if low.startswith("show ip nat translations"):
            if not isinstance(dev, Router):
                return "% Not a router\n"
            rows = ["Proto  Inside               Outside"]
            for b in dev.nat_rows():
                rows.append(f"{b['proto']:<6} {b['inside']:<20} {b['outside']}")
            return "\n".join(rows) + "\n"
        if low.startswith("show access-lists"):
            if not isinstance(dev, Router):
                return "% Not a router\n"
            rows = []
            for ifname, rules in {**dev.acl_in}.items():
                rows.append(f"interface {ifname} in:")
                rows += [f"  {r.as_dict()}" for r in rules]
            for ifname, rules in {**dev.acl_out}.items():
                rows.append(f"interface {ifname} out:")
                rows += [f"  {r.as_dict()}" for r in rules]
            return ("\n".join(rows) or "no access lists configured") + "\n"
        if low.startswith(("show dhcp", "show ip dhcp binding")):
            if not isinstance(dev, Router) or not dev.dhcp_pools:
                return "% No DHCP service\n"
            rows = ["IP address       MAC address          Pool"]
            for pool in dev.dhcp_pools:
                for mac, ip in pool.leases.items():
                    rows.append(f"{ip!s:<16} {mac:<20} {pool.network}")
            return "\n".join(rows) + "\n"
        if low.startswith("show qos interface"):
            return self._show_qos_iface(low.split())
        return "% Invalid show command\n"

    def _show_qos_iface(self, words: list[str]) -> str:
        """show qos interface [<name>] — per-class tx/drops table (§2.6)."""
        dev = self.device
        iface_name = words[3] if len(words) >= 4 else None
        ifaces = (
            [dev.interfaces[iface_name]]
            if iface_name and iface_name in dev.interfaces
            else list(dev.interfaces.values())
        )
        if not ifaces:
            return "% No interfaces\n"
        rows = ["Interface        QoS  Class  Tx-Frames  Drops"]
        _names = ("EF", "AF", "BE")
        for i in ifaces:
            enabled = i.attachment is not None and i.attachment.qos.enabled
            state = "on " if enabled else "off"
            for ci, cname in enumerate(_names):
                tx = i.counters.tx_by_class[ci]
                dr = i.counters.drops_queue_by_class[ci]
                rows.append(f"{i.name:<16} {state}  {cname}    {tx:<10} {dr}")
        return "\n".join(rows) + "\n"

    def _cisco_help(self) -> str:
        return (
            "exec: enable | ping <ip|ipv6> [count] | traceroute <ip|ipv6>\n"
            "show: version | ip interface brief | interfaces | ip route | arp |\n"
            "      ipv6 route | ipv6 neighbors | ipv6 interface brief |\n"
            "      mac address-table | vlan | spanning-tree | ip ospf neighbor |\n"
            "      isis neighbors | isis database | ip bgp summary |\n"
            "      ip nat translations | access-lists | dhcp binding\n"
            "config: enable; conf t; interface <name>; ip address <cidr>;\n"
            "        ipv6 address <cidr>; [no] shutdown; switchport mode access|trunk;\n"
            "        switchport access vlan <n>; ip route <prefix> <next-hop>;\n"
            "        ipv6 route <prefix> <next-hop> [iface]; ipv6 nd ra enable; end\n"
        )

    # =========================== MikroTik-like ==================================
    def _mikrotik(self, line: str) -> str:
        low = " ".join(line.lower().split())
        dev = self.device
        if low in ("?", "help"):
            return (
                "/ip address print | /ip route print | /ip arp print |\n"
                "/ipv6 address print | /ipv6 route print | /ipv6 neighbor print |\n"
                "/interface print | /system resource print | /ping <ip|ipv6> [count=N]\n"
            )
        if low == "/ip address print":
            rows = ["#  ADDRESS            INTERFACE"]
            n = 0
            for i in dev.interfaces.values():
                for ip in i.ips:
                    rows.append(f"{n}  {ip!s:<18} {i.name}")
                    n += 1
            return "\n".join(rows) + "\n"
        if low == "/ip route print":
            if not isinstance(dev, Router):
                return "no routes\n"
            rows = ["#  DST-ADDRESS        GATEWAY         DISTANCE"]
            for n, r in enumerate(dev.route_table_rows()):
                gw = r["next_hop"] or r["iface"] or "-"
                rows.append(f"{n}  {r['prefix']:<18} {gw:<15} {r['ad']}")
            return "\n".join(rows) + "\n"
        if low == "/ipv6 address print":
            rows = ["#  ADDRESS                                  INTERFACE"]
            n = 0
            for i in dev.interfaces.values():
                for ip in [i.link_local, *i.ips6]:
                    rows.append(f"{n}  {ip!s:<40} {i.name}")
                    n += 1
            return "\n".join(rows) + "\n"
        if low == "/ipv6 route print":
            if not isinstance(dev, Router):
                return "no routes\n"
            rows = ["#  DST-ADDRESS              GATEWAY                  DISTANCE"]
            for n, r in enumerate(dev.route_table_rows6()):
                gw = r["next_hop"] or r["iface"] or "-"
                rows.append(f"{n}  {r['prefix']:<24} {gw:<24} {r['ad']}")
            return "\n".join(rows) + "\n"
        if low == "/ipv6 neighbor print":
            rows = ["#  ADDRESS                                  MAC-ADDRESS          INTERFACE"]
            nd = getattr(dev, "nd_cache", {})
            for n, (ip, (mac, ifname)) in enumerate(
                sorted(nd.items(), key=lambda kv: str(kv[0]))
            ):
                rows.append(f"{n}  {ip!s:<40} {mac:<20} {ifname}")
            return "\n".join(rows) + "\n"
        if low == "/ip arp print":
            rows = ["#  ADDRESS         MAC-ADDRESS          INTERFACE"]
            arp = getattr(dev, "arp_table", {})
            for n, (ip, (mac, ifname)) in enumerate(sorted(arp.items())):
                rows.append(f"{n}  {ip!s:<15} {mac:<20} {ifname}")
            return "\n".join(rows) + "\n"
        if low == "/interface vrrp print":
            procs = [
                p for p in getattr(dev, "processes", [])
                if getattr(p, "proto", "") == "vrrp"
            ]
            rows = ["#  INTERFACE  VRID  PRIORITY  STATE    V-IP"]
            for n, p in enumerate(procs):
                r = p.status_row()
                rows.append(
                    f"{n}  {r['iface']:<10} {r['vrid']:<5} {r['priority']:<9} "
                    f"{r['state']:<8} {r['vip']}"
                )
            return "\n".join(rows) + "\n"
        if low in ("/interface vxlan print", "/interface bridge vxlan print"):
            vx = next(
                (p for p in getattr(dev, "processes", []) if getattr(p, "proto", "") == "vxlan"),
                None,
            )
            if vx is None:
                return "no vxlan interfaces\n"
            rows = ["#  VNI     MAC                  LOCATION"]
            for n, r in enumerate(vx.mac_vni_rows()):
                rows.append(f"{n}  {r['vni']:<7} {r['mac']:<20} {r['location']}")
            return "\n".join(rows) + "\n"
        if low.startswith(("/queue print", "/queue simple print")):
            rows = ["#  INTERFACE        QOS  CLASS  TX-FRAMES  DROPS"]
            _names = ("EF", "AF", "BE")
            for n, i in enumerate(dev.interfaces.values()):
                enabled = i.attachment is not None and i.attachment.qos.enabled
                state = "on " if enabled else "off"
                for ci, cname in enumerate(_names):
                    tx = i.counters.tx_by_class[ci]
                    dr = i.counters.drops_queue_by_class[ci]
                    rows.append(f"{n}  {i.name:<16} {state}  {cname}    {tx:<10} {dr}")
            return "\n".join(rows) + "\n"
        if low == "/interface print":
            rows = ["#  NAME       MTU   MAC-ADDRESS        RUNNING"]
            for n, i in enumerate(dev.interfaces.values()):
                mtu = i.attachment.mtu if i.attachment else 1500
                rows.append(
                    f"{n}  {i.name:<10} {mtu:<5} {i.mac}  {'yes' if i.is_up else 'no'}"
                )
            return "\n".join(rows) + "\n"
        if low == "/system resource print":
            return (
                f"uptime: {self.net.now:.1f}s (simulated)\n"
                f"board-name: NetGeo {dev.kind}\nversion: ForgeOS sim\n"
            )
        if low.startswith(("/ping ", "ping ")):
            parts = line.split()
            target = parts[1]
            count = 4
            for p in parts[2:]:
                if p.startswith("count="):
                    count = int(p.split("=", 1)[1])
            return self._do_ping(target, count=count)
        return f"bad command name {line}\n"

    # =========================== shared apps ==================================
    def _do_ping(self, target: str, count: int = 4) -> str:
        try:
            dst = ip_address(target)
        except ValueError:
            # try DNS via device cache
            cached = getattr(self.device, "dns_cache", {}).get(target)
            if cached is None:
                return f"% cannot resolve {target}\n"
            dst = cached
        try:
            rep = self.net.ping(self.device.name, dst, count=count)
        except ValueError as exc:
            return f"% {exc}\n"
        d = rep.as_dict()
        lines = [f"Sending {count} ICMP echos to {dst}:"]
        for seq, rtt in enumerate(d["rtts_ms"], start=1):
            lines.append(f"  reply seq={seq} time={rtt} ms")
        for err in d["errors"]:
            lines.append(f"  {err}")
        lines.append(
            f"Success rate {100.0 - d['loss_pct']:.0f}% ({d['received']}/{d['sent']})"
            + (
                f", rtt min/avg/max = {d['min_ms']}/{d['avg_ms']}/{d['max_ms']} ms"
                if d["min_ms"] is not None
                else ""
            )
        )
        return "\n".join(lines) + "\n"

    def _do_traceroute(self, target: str) -> str:
        try:
            dst = ip_address(target)
        except ValueError:
            return f"% cannot resolve {target}\n"
        try:
            tr = self.net.traceroute(self.device.name, dst)
        except ValueError as exc:
            return f"% {exc}\n"
        lines = [f"Tracing route to {dst}:"]
        for hop in tr.as_dict()["hops"]:
            addr = hop["address"] or "*"
            rtt = f"{hop['rtt_ms']} ms" if hop["rtt_ms"] is not None else "timeout"
            lines.append(f"  {hop['hop']:<3} {addr:<16} {rtt}")
        lines.append("Trace complete." if tr.reached else "Destination not reached.")
        return "\n".join(lines) + "\n"

    # ----- helpers -----------------------------------------------------------------
    def _proc(self, proto: str):
        for p in getattr(self.device, "processes", []):
            if getattr(p, "proto", "") == proto:
                return p
        return None
