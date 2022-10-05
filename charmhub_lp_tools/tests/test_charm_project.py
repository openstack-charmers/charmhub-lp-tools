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


class TestCharmProject(unittest.TestCase):
    def setUp(self):
        self.lpt = mock.MagicMock()
        self.project = charm_project.CharmProject(CHARM_CONFIG, self.lpt)

    def test_request_code_import(self):
        self.project.request_code_import(dry_run=False)
        lp_repo = self.lpt.get_git_repository()
        lp_repo.code_import.requestImport.assert_called_with()

    def test_request_code_import_dry_run(self):
        self.project.request_code_import(dry_run=True)
        lp_repo = self.lpt.get_git_repository()
        lp_repo.code_import.requestImport.assert_not_called()
