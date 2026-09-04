"""Pure tests for ip_alloc.link_subnet — no podman needed."""
import ipaddress

from engine.emulation.ip_alloc import link_subnet


def test_deterministic():
    assert link_subnet("link-1") == link_subnet("link-1")


def test_different_links_get_different_subnets():
    seen = {link_subnet(f"link-{i}")[0] for i in range(20)}
    assert len(seen) == 20  # sanity, not collision-proof (see ponytail note in source)


def test_addresses_are_valid_and_in_subnet():
    for link_id in ("a", "b", "c", "link-x", "n3-test-link"):
        cidr, gateway, ip_a, ip_b = link_subnet(link_id)
        net = ipaddress.ip_network(cidr)
        assert net.prefixlen == 29
        for ip in (gateway, ip_a, ip_b):
            assert ipaddress.ip_address(ip) in net
        assert len({gateway, ip_a, ip_b}) == 3  # all distinct


def test_no_overlap_across_sample_links():
    nets = [ipaddress.ip_network(link_subnet(f"link-{i}")[0]) for i in range(50)]
    for i, n1 in enumerate(nets):
        for n2 in nets[i + 1:]:
            assert not n1.overlaps(n2) or n1 == n2
