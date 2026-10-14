import unittest

from cf_node_health.parser import parse_qx, split_endpoint


class ParserTests(unittest.TestCase):
    def test_complete_qx_subscription(self):
        labels = {"HK": "香港", "TW": "台湾", "SG": "新加坡", "US": "美国", "DE": "德国"}
        lines = []
        for block, region in enumerate(("HK", "TW", "SG", "US", "DE")):
            for index in range(block * 10 + 1, block * 10 + 11):
                lines.append(
                    f"vless = edge-{index}.example.com:443, password=00000000-0000-0000-0000-000000000000, "
                    f"obfs=wss, obfs-host=quiet-meadow-pages-qx.pages.dev, obfs-uri=/old/{index}, tag={labels[region]} {index:02d}"
                )
        nodes = parse_qx("\n".join(lines))
        self.assertEqual(50, len(nodes))
        self.assertEqual(("HK", 1), (nodes[0].region, nodes[0].index))
        self.assertEqual(("DE", 50), (nodes[-1].region, nodes[-1].index))

    def test_endpoint_split_ipv6(self):
        self.assertEqual(("2001:db8::1", 8443), split_endpoint("[2001:db8::1]:8443"))


if __name__ == "__main__":
    unittest.main()
