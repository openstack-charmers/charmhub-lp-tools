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

from unittest import mock

from charmhub_lp_tools import ensure_series
from charmhub_lp_tools.tests.base import BaseTest


class TestEnsureSeries(BaseTest):

    def test_ensure_series(self):
        args = mock.MagicMock()
        args.loglevel = 'DEBUG'
        args.i_really_mean_it = False
        args.config_dir = self.lp_builder_config
        args.git_branches = []
        gc = mock.MagicMock()

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

        self.assertRaises(SystemExit, ensure_series.ensure_series, args, gc)
        gc.projects.return_value = [charm_project]
        ensure_series.ensure_series(args, gc)

        charm_project.ensure_series.assert_called_with(branches=[],
                                                       dry_run=True)
        charm_project.ensure_series.reset_mock()
        git_branches = ['stable/jammy']
        args.git_branches = git_branches
        ensure_series.ensure_series(args, gc)

        charm_project.ensure_series.assert_called_with(branches=git_branches,
                                                       dry_run=True)
