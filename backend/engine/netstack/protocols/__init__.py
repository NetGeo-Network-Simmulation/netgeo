"""Dynamic routing protocol processes that attach to a Router."""
from engine.netstack.protocols.bgp import BgpProcess
from engine.netstack.protocols.isis import IsisProcess
from engine.netstack.protocols.mpls import L3vpnProcess, LdpProcess
from engine.netstack.protocols.ospf import OspfProcess

__all__ = ["BgpProcess", "IsisProcess", "L3vpnProcess", "LdpProcess", "OspfProcess"]
