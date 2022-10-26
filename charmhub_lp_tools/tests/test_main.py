import requests_mock

from unittest import mock
from charmhub_lp_tools import main
from charmhub_lp_tools.charm_project import CharmChannel
from charmhub_lp_tools.tests.base import BaseTest


class TestCopyChannel(BaseTest):
    def setUp(self):
        super().setUp()
        self.args = mock.MagicMock()
        self.args.charms = ['awesome']
        self.args.src_channel = 'latest/edge'
        self.args.dst_channel = 'yoga/edge'
        self.args.force = False
        self.args.confirmed = False
        self.args.close_dst_channel_before = False
        self.args.bases = ['22.04']
        self.gc = mock.MagicMock()
        self.gc.projects.return_value = [self.project]

    def test_copy_channel_invalid_channel(self):
        self.args.dst_channel = 'invalid/edge'
        self.assertRaises(ValueError,
                          main.copy_channel, self.args, self.gc)

    @mock.patch('charmhub_lp_tools.charm_project.get_store_client')
    def test_copy_channel_force(self, get_store_client):
        store_client = mock.MagicMock()
        store_client.list_releases.return_value = ([], [], [])
        get_store_client.return_value = store_client
        self.args.dst_channel = 'invalid/edge'
        self.args.force = True
        with requests_mock.Mocker() as m:
            m.get(CharmChannel.INFO_URL.format(charm='awesome'),
                  json=self.awesome_info)
            revs = main.copy_channel(self.args, self.gc)
            self.assertIn('awesome', revs)
            self.assertEqual(revs['awesome'], {96, 93, 94, 95})
