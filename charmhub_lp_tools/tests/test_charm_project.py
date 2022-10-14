import os
import subprocess

from unittest import mock

import requests_mock

from charmhub_lp_tools import charm_project
from charmhub_lp_tools.exceptions import CharmcraftError504
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


class TestCharmChannel(BaseTest):
    def test_decode_channel_map(self):

        with requests_mock.Mocker() as m:
            m.get(charm_project.CharmChannel.INFO_URL.format(charm='awesome'),
                  json=self.awesome_info)

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
