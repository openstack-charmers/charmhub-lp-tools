import unittest

from charmhub_lp_tools.exceptions import InvalidRiskLevel
from charmhub_lp_tools import parsers


class TestParseChannel(unittest.TestCase):
    def test_parse_channel(self):
        for value, expected in [('foo/stable', ('foo', 'stable')),
                                ('edge', ('latest', 'edge')),
                                ('foo', ('foo', 'stable')),
                                ]:
            self.assertEqual(parsers.parse_channel(value),
                             expected)

    def test_invalid_risk_level(self):
        self.assertRaises(InvalidRiskLevel, parsers.parse_channel, 'foo/bar')
