import json
import os
import unittest

import yaml

from unittest import mock

from charmhub_lp_tools import charm_project

CHARM_CONFIG_STR = """
name: Awesome Charm
charmhub: awesome
launchpad: charm-awesome
team: awesome-charmers
repo: https://github.com/canonical/charm-awesome-operator
branches:
  main:
    channels:
      - yoga/edge
      - latest/edge
  stable/xena:
    channels:
      - xena/edge
"""
CHARM_CONFIG = yaml.safe_load(CHARM_CONFIG_STR)


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.lpt = mock.MagicMock()
        self.project = charm_project.CharmProject(CHARM_CONFIG, self.lpt)
        with open(os.path.join(os.path.dirname(__file__), 'fixtures',
                               'awesome-info.json')) as f:
            self.awesome_info = json.load(f)
