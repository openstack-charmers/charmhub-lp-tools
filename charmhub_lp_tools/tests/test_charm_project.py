import json
import os
import unittest
import yaml
import requests_mock

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


class TestCharmChannel(unittest.TestCase):
    def setUp(self):
        self.lpt = mock.MagicMock()
        self.project = charm_project.CharmProject(CHARM_CONFIG, self.lpt)

    def test_decode_channel_map(self):
        with open(os.path.join(os.path.dirname(__file__), 'fixtures',
                               'awesome-info.json')) as f:
            awesome_info = json.load(f)
        with requests_mock.Mocker() as m:
            m.get(charm_project.CharmChannel.INFO_URL.format(charm='awesome'),
                  json=awesome_info)

            charm_channel = charm_project.CharmChannel(self.project,
                                                       'yoga/stable')
            self.assertEqual(charm_channel.decode_channel_map('22.04'),
                             {79})
            charm_channel = charm_project.CharmChannel(self.project,
                                                       'latest/edge')
            self.assertEqual(charm_channel.decode_channel_map('22.04'),
                             {96, 93, 94, 95})

    def test_release(self):
        charm_channel = charm_project.CharmChannel(self.project,
                                                   'yoga/stable')
        with mock.patch('subprocess.run') as run:
            charm_channel.release(96, dry_run=False, check=True)
            run.assert_called_with(('charmcraft release awesome --revision=96 '
                                    '--channel=yoga/stable'),
                                   check=True)
            run.reset_mock()
            with mock.patch('builtins.print') as print:
                charm_channel.release(96, dry_run=True, check=True)
                print.assert_called_with(('charmcraft release awesome '
                                          '--revision=96 '
                                          '--channel=yoga/stable'),
                                         ' # dry-run mode')

    def test_close(self):
        charm_channel = charm_project.CharmChannel(self.project,
                                                   'yoga/stable')
        with mock.patch('subprocess.run') as run:
            charm_channel.close(dry_run=False, check=True)
            run.assert_called_with('charmcraft close awesome yoga/stable',
                                   check=True)
            run.reset_mock()
            with mock.patch('builtins.print') as print:
                charm_channel.close(dry_run=True, check=True)
                print.assert_called_with(('charmcraft close awesome '
                                          'yoga/stable'),
                                         ' # dry-run mode')
