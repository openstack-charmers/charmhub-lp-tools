import io
import json
import os
import shutil
import tempfile

import requests_mock

from charmhub_lp_tools import charm_project
from charmhub_lp_tools import reports
from charmhub_lp_tools.tests.base import BaseTest


class TestCharmhubReports(BaseTest):
    def setUp(self):
        super().setUp()
        self.tmpdir = tempfile.mkdtemp(prefix='charmhub-lp-tool-')

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_html(self):
        self._check_report(reports.HtmlCharmhubReport,
                           output=self.tmpdir)
        self.assertEqual(sorted(os.listdir(self.tmpdir)),
                         sorted(['index.html', 'openstack-yoga.html']))
        with open(os.path.join(self.tmpdir, 'openstack-yoga.html'), 'r') as f:
            content = f.read()

            self._check_revisions_in_report(content)

    def test_plain(self):
        stdout = io.StringIO()
        self._check_report(reports.PlainCharmhubReport,
                           output=stdout)
        self._check_revisions_in_report(stdout.getvalue())

    def test_json(self):
        stdout = io.StringIO()
        self._check_report(reports.JSONCharmhubReport,
                           output=stdout)
        r = json.loads(stdout.getvalue())
        self.assertIn('openstack', r)
        self.assertIn('yoga', r['openstack'])
        self.assertIn(self.project.charmhub_name, r['openstack']['yoga'])
        self.assertIn('stable',
                      r['openstack']['yoga'][self.project.charmhub_name])
        for c in r['openstack']['yoga'][self.project.charmhub_name]['stable']:
            self.assertEqual(c['revision']['revision'], 79)

    def _check_report(self, klass, output):
        report = klass(output)
        with requests_mock.Mocker() as m:
            m.get(charm_project.CharmChannel.INFO_URL.format(charm='awesome'),
                  json=self.awesome_info)

            channel = charm_project.CharmChannel(self.project, 'yoga/stable')
            for channel_def in channel.channel_map:
                if (channel_def['channel']['track'],
                        channel_def['channel']['risk']) == ('yoga', 'stable'):
                    from pprint import pprint
                    pprint(channel_def)
                    report.add_revision(channel, channel_def)
            report.generate()

        return report

    def _check_revisions_in_report(self, content):
        # focal
        self.assertIn('79 (20.04/amd64)', content)
        self.assertIn('79 (20.04/arm64)', content)
        self.assertIn('79 (20.04/ppc64el)', content)
        self.assertIn('79 (20.04/s390x)', content)
        # jammy
        self.assertIn('79 (22.04/amd64)', content)
        self.assertIn('79 (22.04/arm64)', content)
        self.assertIn('79 (22.04/ppc64el)', content)
        self.assertIn('79 (22.04/s390x)', content)
