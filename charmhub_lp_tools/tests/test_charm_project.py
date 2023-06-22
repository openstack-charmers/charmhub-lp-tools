import os
import subprocess

from datetime import datetime
from unittest import mock

import requests_mock

from charmhub_lp_tools import charm_project
from charmhub_lp_tools.exceptions import CharmcraftError504
from charmhub_lp_tools.schema import DEFAULT_SERIES_STATUS
from charmhub_lp_tools.tests.base import BaseTest


class TestCharmProject(BaseTest):

    def test_request_code_import(self):
        self.project.request_code_import(dry_run=False)
        lp_repo = self.lpt.get_git_repository()
        lp_repo.code_import.requestImport.assert_called_with()

    def test_request_code_import_dry_run(self):
        self.project.request_code_import(dry_run=True)
        lp_repo = self.lpt.get_git_repository()
        lp_repo.code_import.requestImport.assert_not_called()

    def test_channels(self):
        self.assertEqual(
            self.project.channels,
            {charm_project.CharmChannel(self.project, 'latest/edge'),
             charm_project.CharmChannel(self.project, 'yoga/edge'),
             charm_project.CharmChannel(self.project, 'xena/edge'),
             }
        )

    def test_get_builds(self):
        with mock.patch.object(
                self.project.lpt, 'get_charm_recipes'
        ) as get_charm_recipes:
            get_charm_recipes.return_value = self._gen_recipes_and_builds()

            expected_total_builds = 8  # 1 build per arch (4) and branch (2).
            num_total_builds = 0
            for recipe, build in self.project.get_builds({'yoga/edge',
                                                          'latest/edge'}):
                num_total_builds += 1
                # xena recipes should have been filtered out
                self.assertNotIn('xena', recipe.name)

            self.assertEqual(num_total_builds, expected_total_builds)

    def test_ensure_series(self):
        dry_run = False
        branch_info = {}
        branch_info['jammy'] = {'enabled': True,
                                'series-active': True,
                                'series-summary': 'my summary',
                                'series-status': DEFAULT_SERIES_STATUS,
                                'series-title': 'my title'}
        branch_info['focal'] = {'enabled': False,
                                'series-active': True,
                                'series-summary': None,
                                'series-status': DEFAULT_SERIES_STATUS,
                                'series-title': None}
        branch_info['master'] = {'enabled': True,
                                 'series-active': True,
                                 'series-summary': None,
                                 'series-status': DEFAULT_SERIES_STATUS,
                                 'series-title': None}

        with mock.patch.object(self.project, 'create_series') as create_series:
            fake_series = mock.MagicMock()
            create_series.return_value = fake_series
            master = mock.MagicMock()
            master.__getitem__.side_effect = branch_info['master'].__getitem__
            jammy = mock.MagicMock()
            jammy.__getitem__.side_effect = branch_info['jammy'].__getitem__
            focal = mock.MagicMock()
            focal.__getitem__.side_effect = branch_info['focal'].__getitem__

            self.project._lp_series = {}
            self.project.branches = {
                'refs/heads/master': master,
                'refs/heads/stable/jammy': jammy,
                'refs/heads/stable/focal': focal,
            }
            result = self.project.ensure_series(['stable/jammy'],
                                                dry_run=dry_run)
            self.assertEqual(result, {'jammy': fake_series})
            self.project.create_series.assert_called_with(
                'jammy', branch_info['jammy']['series-summary'], dry_run)
            fake_series.lp_save.assert_called_with()
            self.assertEqual(fake_series.active,
                             branch_info['jammy']['series-active'])
            self.assertEqual(fake_series.status,
                             branch_info['jammy']['series-status'])
            self.assertEqual(fake_series.title,
                             branch_info['jammy']['series-title'])
            self.assertEqual(fake_series.summary,
                             branch_info['jammy']['series-summary'])

    def test_create_series(self):
        with mock.patch.object(self.project, 'log') as log:
            self.assertEqual(self.project.create_series('my-series',
                                                        'my shiny summary',
                                                        dry_run=True),
                             None)
            log.info.assert_called_with(('NOT creating the series %s with '
                                         'summary %s (dry-run mode)'),
                                        'my-series', 'my shiny summary')

        with mock.patch.object(self.project, 'lpt') as lpt:
            fake_series = mock.MagicMock()
            lpt.create_project_series.return_value = fake_series
            series = self.project.create_series('my-series',
                                                'my shiny new summary',
                                                dry_run=False)
            lpt.create_project_series.assert_called_with(
                self.project.lp_project,
                name='my-series',
                summary='my shiny new summary')
            self.assertEqual(series, fake_series)

    def test_lp_series(self):
        s_trunk = mock.MagicMock()
        s_trunk.name = 'trunk'
        s_jammy = mock.MagicMock()
        s_jammy.name = 'jammy'
        s_focal = mock.MagicMock()
        s_focal.name = 'focal'

        with mock.patch.object(self.project, '_lp_project') as _lp_project:
            _lp_project.series = [s_trunk, s_jammy, s_focal]
            self.assertEqual(self.project.lp_series,
                             {'jammy': s_jammy,
                              'trunk': s_trunk,
                              'focal': s_focal})

    def _gen_recipes_and_builds(self):
        recipes = []
        for git_branch, store_channel in [
                ('master', 'latest/edge'),
                ('stable/yoga', 'yoga/edge'),
                ('stable/xena', 'xena/edge')]:
            recipe = mock.MagicMock()
            recipe.name = (f'{self.project.charmhub_name}.'
                           f'{git_branch.replace("/", "-")}.'
                           f'{store_channel.split("/")[0]}')
            recipe.store_channels = [store_channel]

            builds = []
            for revision_id, datebuilt in [
                    ('11d5167158607b2211d14546b0a4f96952dfdb82',
                     datetime(2022, 4, 27, 1, 0, 0)),
                    ('6cbb9d7ed45fa395b1cc8d2f87fe90e3461b68de',
                     datetime(2022, 4, 26, 2, 0, 0)),
                    ('bb4dad842d7d36af1ea422fb828249e012143230',
                     datetime(2022, 4, 25, 3, 0, 0)),
                    (None,
                     None)]:
                for arch in ['s390x', 'amd64', 'ppc64el', 'arm64']:
                    build = mock.MagicMock()
                    build.distro_arch_series._architecture_tag = arch
                    build.distro_series.name = 'jammy'
                    build.revision_id = revision_id
                    build.datebuilt = datebuilt
                    builds.append(build)

            recipe.builds = builds
            recipes.append(recipe)
        return recipes


class TestCharmChannel(BaseTest):
    def test_decode_channel_map(self):

        with requests_mock.Mocker() as m:
            m.get(charm_project.CharmProject.INFO_URL.format(charm='awesome'),
                  json=self.awesome_info)

            charm_channel = charm_project.CharmChannel(self.project,
                                                       'yoga/stable')
            self.assertEqual(charm_channel
                             .get_revisions_for_bases(bases=['22.04']),
                             [79])
            charm_channel = charm_project.CharmChannel(self.project,
                                                       'latest/edge')
            self.assertEqual(charm_channel
                             .get_revisions_for_bases(bases=['22.04']),
                             [93, 94, 95, 96])

    @mock.patch('charmhub_lp_tools.charm_project.get_store_client')
    def test_release(self, get_store_client):
        store_client = mock.MagicMock()
        store_client.list_releases.return_value = ([], [], [])
        get_store_client.return_value = store_client
        charm_channel = charm_project.CharmChannel(self.project,
                                                   'yoga/stable')

        with mock.patch('subprocess.run') as run:
            charm_channel.release(96, dry_run=False, check=True)
            run.assert_called_with(['charmcraft', 'release', 'awesome',
                                    '--revision=96', '--channel=yoga/stable'],
                                   check=True,
                                   text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
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
            run.assert_called_with(['charmcraft', 'close', 'awesome',
                                    'yoga/stable'],
                                   check=True,
                                   text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
            run.reset_mock()
            with mock.patch('builtins.print') as print:
                charm_channel.close(dry_run=True, check=True)
                print.assert_called_with(('charmcraft close awesome '
                                          'yoga/stable'),
                                         ' # dry-run mode')


class TestRunCharmcraft(BaseTest):
    @mock.patch('subprocess.run')
    def test_run_charmcraft_error504(self, run):
        RETRIES = 4
        cmd = ['charmcraft', 'close', 'awesome', 'foo/edge']

        def raise_error(*args, **kwargs):
            with open(os.path.join(os.path.dirname(__file__), 'fixtures',
                                   'charmcraft-error-504.txt')) as f:
                raise subprocess.CalledProcessError(1, cmd, output=f.read())

        run.side_effect = raise_error
        self.assertRaises(CharmcraftError504,
                          charm_project.run_charmcraft, cmd, check=True,
                          retries=RETRIES)
        run.assert_has_calls([mock.call(cmd, check=True,
                                        text=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)] * RETRIES)

    @mock.patch('subprocess.run')
    def test_run_charmcraft(self, run):
        cmd = ['charmcraft', 'close', 'awesome', 'foo/edge']

        def raise_error(*args, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, output='some error')

        run.side_effect = raise_error
        self.assertRaises(subprocess.CalledProcessError,
                          charm_project.run_charmcraft, cmd, check=True,
                          retries=3)
        run.assert_has_calls([mock.call(cmd, check=True,
                                        text=True,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)])
