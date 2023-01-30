import unittest

import yaml

from charmhub_lp_tools import schema


INVALID_CONFIG = """
defaults:
  branches: {}
"""

VALID_CONFIGS = [
    """
defaults:
  team: openstack-charmers
  branches:
    master:
      build-channels:
        charmcraft: "1.7/stable"
      channels:
        - latest/edge
    stable/queens:
      enabled: False
      build-channels:
        charmcraft: "1.5/stable"
      channels:
        - queens/edge
projects:
  - name: OpenStack Aodh Charm
    charmhub: aodh
    launchpad: charm-aodh
    repository: https://opendev.org/openstack/charm-aodh.git

  - name: OpenStack Barbican Vault Charm
    charmhub: barbican-vault
    launchpad: charm-barbican-vault
    repository: https://opendev.org/openstack/charm-barbican-vault.git
    branches:
      master:
        build-channels:
          charmcraft: "1.7/stable"
        channels:
          - latest/edge
      stable/rocky:
        enabled: False
        build-channels:
          charmcraft: "1.5/stable"
        channels:
          - rocky/edge
    """,
]


class TestConfigSchema(unittest.TestCase):
    def test_valid_config_file(self):

        for fixture in VALID_CONFIGS:
            data = yaml.safe_load(fixture)
            self.assertTrue(schema.config_schema.is_valid(data),
                            schema.config_schema.validate(data))

    def test_invalid_config_file(self):
        data = yaml.safe_load(INVALID_CONFIG)
        self.assertFalse(schema.config_schema.is_valid(data))
