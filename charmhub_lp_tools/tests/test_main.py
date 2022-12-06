import os
import shutil
import tempfile

import requests_mock

from unittest import mock
from charmhub_lp_tools import main
from charmhub_lp_tools.charm_project import CharmProject, CharmChannel
from charmhub_lp_tools.tests.base import BaseTest


class TestCopyChannel(BaseTest):
    def setUp(self):
        super().setUp()
        self.args = mock.MagicMock()
        self.args.charms = ['awesome']
        self.args.src_channel = 'latest/edge'
        self.args.dst_channel = 'yoga/edge'
        self.args.confirmed = False
        self.args.close_dst_channel_before = False
        self.args.bases = ['22.04']
        self.gc = mock.MagicMock()
        self.gc.projects.return_value = [self.project]

    @mock.patch('charmhub_lp_tools.main.logger')
    @mock.patch('charmhub_lp_tools.charm_project.get_store_client')
    def test_copy_channel_invalid_channel(self,
                                          get_store_client,  # type: ignore
                                          mock_logger):  # type: ignore
        store_client = mock.MagicMock()
        get_store_client.return_value = store_client
        self.args.dst_channel = 'invalid/edge'
        main.copy_channel(self.args, self.gc)
        self.assertIn(mock.call(mock.ANY, "invalid", mock.ANY),
                      mock_logger.error.call_args_list)

    @mock.patch('charmhub_lp_tools.charm_project.get_store_client')
    def test_copy_channel(self, get_store_client):  # type: ignore
        store_client = mock.MagicMock()
        store_client.list_releases.return_value = ([], [], [])
        get_store_client.return_value = store_client
        self.args.dst_channel = 'invalid/edge'
        self.args.force = True
        with mock.patch.object(CharmChannel,
                               "get_charm_metadata_for_channel",
                               return_value={}):
            with requests_mock.Mocker() as m:
                m.get(CharmProject.INFO_URL.format(charm='awesome'),
                      json=self.awesome_info)
                revs = main.copy_channel(self.args, self.gc)
                self.assertIn('awesome', revs)
                self.assertEqual(revs['awesome'], {96, 93, 94, 95})


class TestCharmhubReport(BaseTest):
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(suffix='.ch-report')
        self.args = mock.MagicMock()
        self.args.charms = ['awesome']
        self.args.tracks = ['foo', 'xena']
        self.args.format = 'html'
        self.args.output = self.tmpdir
        self.gc = mock.MagicMock()
        self.gc.projects.return_value = [self.project]

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ch_report_main(self):
        with requests_mock.Mocker() as m:
            m.get(CharmProject.INFO_URL.format(charm='awesome'),
                  json=self.awesome_info)
            main.ch_report_main(self.args, self.gc)

        self.assertEqual(sorted(os.listdir(self.tmpdir)),
                         sorted(['index.html', 'openstack-xena.html']))

        with open(os.path.join(self.tmpdir, 'openstack-xena.html'), 'r') as f:
            report_content = f.read()
            self.assertIn('awesome', report_content)
            self.assertIn('80 (20.04/amd64)', report_content)
            self.assertIn('80 (20.04/arm64)', report_content)
            self.assertIn('80 (20.04/ppc64el)', report_content)
            self.assertIn('80 (20.04/s390x)', report_content)

        with open(os.path.join(self.tmpdir, 'index.html'), 'r') as f:
            self.assertIn('href="openstack-xena.html"', f.read())
            with mock.patch.object(CharmChannel,
                                   "get_charm_metadata_for_channel",
                                   return_value={}):
                with requests_mock.Mocker() as m:
                    m.get(CharmProject.INFO_URL.format(charm='awesome'),
                          json=self.awesome_info)
                    revs = main.copy_channel(self.args, self.gc)
                    self.assertIn('awesome', revs)
                    self.assertEqual(revs['awesome'], {96, 93, 94, 95})
