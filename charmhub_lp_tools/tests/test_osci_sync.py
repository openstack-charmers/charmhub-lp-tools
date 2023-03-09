#
# Copyright (C) 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import pathlib
import pprint
import unittest

from unittest import mock

from charmhub_lp_tools import osci_sync
from charmhub_lp_tools.exceptions import CharmNameNotFound


class TestOsciSync(unittest.TestCase):
    def setUp(self):
        self.fake_repo_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'fixtures/fake_repo/'))
        self.git_repo = mock.MagicMock()
        self.git_repo.git.rev_parse.return_value = self.fake_repo_path

    def test_find_osci_yaml(self):
        self.assertEqual(
            osci_sync.find_osci_yaml(self.git_repo),
            pathlib.Path(os.path.join(self.fake_repo_path, 'osci.yaml'))
        )

    def test_find_osci_yaml_not_found(self):
        self.git_repo.git.rev_parse.return_value = os.path.join(
            self.fake_repo_path, '..')
        self.assertRaises(
            FileNotFoundError,
            osci_sync.find_osci_yaml, self.git_repo,
        )

    def test_load_osci_yaml(self):
        osci = osci_sync.load_osci_yaml(self.git_repo)
        self.assertTrue(osci[0]['project']['vars']['needs_charm_build'])

    def test_get_charm_name(self):
        osci = [{'project': {'vars': {'charm_build_name': 'fake'}}}]
        self.assertEqual(osci_sync.get_charm_name(osci), 'fake')
        self.assertRaises(CharmNameNotFound, osci_sync.get_charm_name, [{}])
        self.assertRaises(CharmNameNotFound, osci_sync.get_charm_name,
                          [{'project': {}}])

    def test_get_project_vars(self):
        osci = [{'project': {'vars': {'charm_build_name': 'fake'}}}]
        self.assertEqual(osci_sync.get_project_vars(osci),
                         {'charm_build_name': 'fake'})

    def test_gen_auto_build_channel(self):
        lp_key = 'charmcraft'
        auto_build_channels = {lp_key: '1.5/stable',
                               'core18': 'latest/edge'}
        osci_key = 'charmcraft_channel'
        project_vars = {osci_key: '2.1/stable'}
        changed = osci_sync.gen_auto_build_channel(
            auto_build_channels,
            project_vars,
            [(lp_key, osci_key, None)])
        expected = {lp_key: '2.1/stable',
                    'core18': 'latest/edge'}
        self.assertDictEqual(changed, expected)

    def test_setup_parser(self):
        parser = argparse.ArgumentParser(description='Test')
        subparser = parser.add_subparsers(required=True, dest='cmd')
        osci_sync.setup_parser(subparser)
        self.assertIn('osci-sync', subparser.choices)
        self.assertEqual(subparser.choices['osci-sync'].get_default('func'),
                         osci_sync.main)
        self.assertIn('--i-really-mean-it',
                      subparser.choices['osci-sync'].format_help())

    @mock.patch.object(osci_sync, 'logger')
    @mock.patch('git.Repo')
    def test_main(self, Repo, logger):
        args = mock.MagicMock()
        args.loglevel = 'DEBUG'
        args.repo_dir = self.fake_repo_path
        args.i_really_mean_it = False
        gc = mock.MagicMock()
        Repo.return_value = self.git_repo

        charm_project = mock.MagicMock()
        charm_project.lp_project.name = 'charm-fake'
        charm_project.branches = {
            'refs/heads/stable/jammy': {'channels': ['22.04/edge']},
        }
        recipe = mock.MagicMock()
        recipe.name = 'charm-fake.stable-jammy.22.04'
        recipe.web_link = 'https://example.com/charm-fake/%s' % recipe.name
        recipe.auto_build_channels = {'core18': 'latest/edge'}
        recipes = [recipe]
        charm_project.lpt.get_charm_recipes.return_value = recipes
        gc.projects.return_value = [charm_project]

        osci_sync.main(args, gc)
        logger.info.assert_any_call('Using recipe %s', recipe.web_link)
        logger.info.assert_any_call(
            'The auto build channels have changed: %s',
            pprint.pformat({'charmcraft': '2.0/stable'}),
        )
        logger.info.assert_any_call('Dry-run mode: NOT committing the changes')
        recipe.lp_save.assert_not_called()

        # re-run with --i-really-mean-it
        recipe.reset_mock()
        recipe.name = 'charm-fake.stable-jammy.22.04'
        recipe.web_link = 'https://example.com/charm-fake/%s' % recipe.name
        recipe.auto_build_channels = {'core18': 'latest/edge'}
        args.i_really_mean_it = True
        osci_sync.main(args, gc)
        self.assertDictEqual(recipe.auto_build_channels,
                             {'core18': 'latest/edge',
                              'charmcraft': '2.0/stable'})
        recipe.lp_save.assert_called_with()

        # re-run with a missing recipe
        charm_project.lpt.get_charm_recipes.return_value = []
        args.i_really_mean_it = False
        with mock.patch('sys.exit') as sys_exit:
            def fake_exit(code):
                self.assertEqual(code, 2)
                raise SystemExit(2)
            sys_exit.side_effect = fake_exit
            self.assertRaises(SystemExit, osci_sync.main, args, gc)
            logger.error.assert_any_call('Recipe %s not found in %s',
                                         'charm-fake.stable-jammy.22.04',
                                         charm_project)
