# Copyright 2023 Canonical
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Base classes for testing."""

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
project_group: openstack
"""
CHARM_CONFIG = yaml.safe_load(CHARM_CONFIG_STR)


class BaseTest(unittest.TestCase):
    """Base class for test cases."""

    def setUp(self):
        """Set up base class."""
        self.lpt = mock.MagicMock()
        self.project = charm_project.CharmProject(CHARM_CONFIG, self.lpt)
        self.project._charmhub_tracks = ['yoga', 'latest', 'xena']
        with open(os.path.join(os.path.dirname(__file__), 'fixtures',
                               'awesome-info.json')) as f:
            self.awesome_info = json.load(f)
        self.lp_builder_config = os.path.join(os.path.dirname(__file__),
                                              'fixtures', 'lp-builder-config')
