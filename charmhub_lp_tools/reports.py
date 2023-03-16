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
"""Logic to generate reports.

The two sources of information to produce the reports are Launchpad and
Charmhub.
"""
import abc
import collections
import io
import json
import logging
import operator
import os
import sys

from datetime import datetime
from typing import (
    List,
    Optional,
    Union,
)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import humanize

from jinja2 import Environment, FileSystemLoader, select_autoescape
from prettytable import PrettyTable

from .charm_project import CharmChannel
from .launchpadtools import TypeLPObject
from .parsers import parse_channel


NOW = datetime.now(tz=ZoneInfo("UTC"))


class BaseReport(abc.ABC):
    """Abstract class to implement a report."""

    @property
    def templates_dirs(self):
        """List of directories that contain templates."""
        return [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'templates'),
        ]

    def _get_jinja2_env(self):
        return Environment(
            loader=FileSystemLoader(self.templates_dirs),
            extensions=["jinja2_humanize_extension.HumanizeExtension"],
            autoescape=select_autoescape()
        )


class BaseBuildsReport(BaseReport):
    """Base class for builds report."""

    def __init__(self, output: str = None):
        """Initialize base report."""
        self.output = output
        self.log = logging.getLogger(f'{__name__}.{self.__class__.__name__}')

    @abc.abstractmethod
    def add_build(self, charm_project, recipe, build: TypeLPObject):
        """Add a new build object to the report.

        :param charm_project: charm project the build belongs to.
        :param build: build to add to the report.
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def generate(self):
        """Generate report."""
        raise NotImplementedError()


class HtmlBuildsReport(BaseBuildsReport):
    """Specialized class to implement HTML reports."""

    def __init__(self, output):
        super().__init__(output)
        # {'openstack': {'yoga': {'edge': [<build>, <build>,...]}}}
        self.builds = collections.defaultdict(
            lambda: collections.defaultdict(
                lambda: collections.defaultdict(
                    list
                )
            )
        )

    def add_build(self, charm_project, recipe, build):
        """Add a build to the report."""
        # the same build could have been used to be released into multiple
        # channels.
        for channel in recipe.store_channels:
            (track, risk) = parse_channel(channel)
            self.builds[charm_project.project_group][track][risk].append(
                (charm_project, recipe, build)
            )

    def generate(self):
        """Generate a report and write to ``self.output``."""
        reports_written = collections.defaultdict(
            lambda: collections.defaultdict(dict)
        )
        os.makedirs(self.output, exist_ok=True)
        env = self._get_jinja2_env()
        template = env.get_template('all_builds.html.j2')
        for project, tracks in self.builds.items():
            for track, risks in tracks.items():
                for risk, builds in risks.items():
                    channel = f'{track}/{risk}'
                    content = template.stream({'all_builds': builds,
                                               'project': project,
                                               'track': track,
                                               'risk': risk,
                                               'channel': channel,
                                               'NOW': NOW})

                    fname = f'{project}-{track}-{risk}.html'
                    report_file = os.path.join(self.output, fname)
                    self.log.info('writing html report to %s for channel %s',
                                  report_file, channel)
                    with open(report_file, 'w') as f:
                        content.dump(f)
                        f.write('\n')

                    reports_written[project][track][risk] = fname

        # generate index.
        template = env.get_template('index.html.j2')
        content = template.stream({'reports': reports_written,
                                   'NOW': NOW})

        with open(os.path.join(self.output, 'index.html'), 'w') as f:
            self.log.info('Writing html report index to %s', f.name)
            content.dump(f)
            f.write('\n')


class PlainBuildsReport(BaseBuildsReport):
    """Specialized class to implement plain text reports of builds."""

    def __init__(self, output: str = None):
        """Initialize plain builds report."""
        super().__init__(output)
        self.t = PrettyTable()
        self.cols = ['Recipe Name', 'Channels', 'Arch', 'State', 'Age',
                     'Revision', 'Store Rev', 'Build Log']
        self.t.field_names = self.cols
        self.t.align = 'l'  # align to the left.

    def add_build(self, charm_project, recipe, build):
        """Add a build to the report."""
        build_arch_tag = build.distro_arch_series.architecture_tag
        series_arch = f'{build.distro_series.name}/{build_arch_tag}'
        if build.buildstate != 'Successfully built':
            build_log = build.build_log_url
        else:
            build_log = ''

        try:
            # git commit hash short version
            revision = build.revision_id[:7]
        except Exception:
            self.log.debug('Cannot get git commit hash short version: %s',
                           build.revision_id)
            revision = None

        if build.store_upload_status == 'Uploaded':
            store_rev = build.store_upload_revision
        else:
            store_rev = build.store_upload_error_message

        row = [
            recipe.name,
            ', '.join(recipe.store_channels),
            series_arch,
            build.buildstate,
            humanize.naturaltime(build.datebuilt, when=NOW),
            revision,
            store_rev,
            build_log,
        ]
        self.t.add_row(row)

    def generate(self):
        """Generate a report and print it to stdout."""
        print(self.t.get_string(sort_key=operator.itemgetter(0, 1, 2),
                                sortby="Recipe Name"))


class JSONBuildsReport(BaseBuildsReport):
    """Specialized class to implement a report of builds in JSON."""

    def __init__(self, output):
        super().__init__(output)
        self.builds = collections.defaultdict(dict)

    def add_build(self, charm, recipe, build):
        """Add a build to the report."""
        build_arch_tag = build.distro_arch_series.architecture_tag
        series_arch = f'{build.distro_series.name}/{build_arch_tag}'
        self.builds[recipe.name][series_arch] = {
            'datebuilt': build.datebuilt,
            'store_channels': recipe.store_channels,
            'buildstate': build.buildstate,
            'build_log_url': build.build_log_url,
            'revision': build.revision_id,
            'store_upload_revision': build.store_upload_revision,
            'store_upload_status': build.store_upload_status,
            'store_upload_error_message': build.store_upload_error_message,
            'web_link': build.web_link,
            'recipe_web_link': recipe.web_link,
        }

    def generate(self):
        """Generate a report and print it to stdout."""
        print(json.dumps(self.builds, default=str))


class BaseCharmhubReport(BaseReport):
    """Base Charmhub report class."""

    def __init__(self, output: Optional[Union[str, io.RawIOBase]]):
        self.output = output if output else self.DEFAULT_OUTPUT
        self.log = logging.getLogger(f'{__name__}.{self.__class__.__name__}')
        # {'openstack': {'yoga': {'keystone': {'edge': [<rev>, <rev>,...]}}}
        self.revisions = collections.defaultdict(     # project_group
            lambda: collections.defaultdict(          # track
                lambda: collections.defaultdict(      # charm name
                    lambda: collections.defaultdict(  # risk
                        list
                    )
                )
            )
        )

    def add_revision(
            self,
            channel: CharmChannel,
            revision: int,
    ):
        """Add new revision to the report."""
        pg = channel.project.project_group
        name = channel.project.charmhub_name
        self.log.debug('Adding revision to list: [%s] %s (%s): %s',
                       pg, name, channel.name, revision)
        self.revisions[pg][channel.track][name][channel.risk].append(revision)

    @abc.abstractmethod
    def generate(self):
        """Generate a report and write it to ``self.output``."""
        raise NotImplementedError()


class HtmlCharmhubReport(BaseCharmhubReport):
    """HTML Charmhub report class."""

    DEFAULT_OUTPUT = "./report"

    def generate(self):
        """Generate a report and write it to ``self.output``."""
        reports_written = collections.defaultdict(dict)
        os.makedirs(self.output, exist_ok=True)
        env = self._get_jinja2_env()
        template = env.get_template('charms_per_track.html.j2')
        for project_group, tracks in self.revisions.items():
            for track, charms in tracks.items():
                content = template.stream({'charms': charms,
                                           'track': track,
                                           'project_group': project_group,
                                           'NOW': NOW,
                                           })
                fname = f'{project_group}-{track}.html'
                fpath = os.path.join(self.output, fname)
                with open(fpath, 'w') as f:
                    content.dump(f)
                    f.write('\n')

                reports_written[project_group][track] = fname

        # generate index.
        template = env.get_template('index_charms_per_track.html.j2')
        content = template.stream({'reports': reports_written,
                                   'NOW': NOW})

        with open(os.path.join(self.output, 'index.html'), 'w') as f:
            self.log.info('Writing html report index to %s', f.name)
            content.dump(f)
            f.write('\n')


class JSONCharmhubReport(BaseCharmhubReport):
    """Specialized class to generate Charmhub reports in JSON."""

    DEFAULT_OUTPUT = sys.stdout

    def __init__(self, output: Optional[Union[str, io.RawIOBase]]):
        if isinstance(output, str):
            super().__init__(open(output, 'w'))
        else:
            super().__init__(output)

    def generate(self):
        """Generate a report and write it to ``self.output``."""
        print(json.dumps(self.revisions, default=str), file=self.output)


class PlainCharmhubReport(BaseCharmhubReport):
    """Specialized class to implement charmhub report in plain text."""

    DEFAULT_OUTPUT = sys.stdout

    def __init__(self, output: Optional[Union[str, io.RawIOBase]]):
        if isinstance(output, str):
            super().__init__(open(output, 'w'))
        else:
            super().__init__(output)

    def init_table(self):
        """Initialize table.

        :returns: a PrettyTable instance.
        """
        t = PrettyTable()
        t.field_names = ['Charm',
                         'Edge',
                         'Beta',
                         'Candidate',
                         'Stable']
        t.align = 'l'  # align to the left.
        return t

    def generate(self):
        """Generate a report and write it to ``self.output``."""
        for project_group, tracks in self.revisions.items():
            for track, charms in tracks.items():
                pg_table = self.init_table()
                pg_table.title = f'Group: {project_group} - Track: {track}'
                for charm, risks in charms.items():
                    row = [charm]
                    for risk in ['edge', 'beta', 'candidate', 'stable']:
                        if risk in risks:
                            revs = []
                            for channel_def in risks[risk]:
                                if channel_def:
                                    revision = channel_def['revision']
                                    base = channel_def['channel']['base']
                                    revs.append(
                                        '%s (%s/%s)' % (
                                            revision['revision'],
                                            base['channel'],
                                            base['architecture'],
                                        )
                                    )
                                else:
                                    revs.append('-')
                            row.append('\n'.join(revs))
                        else:
                            row.append('-')
                    pg_table.add_row(row)
                print(pg_table.get_string(sort_key=operator.itemgetter(0),
                                          sortby="Charm"),
                      file=self.output if self.output else sys.stdout)


_build_report_klasses = {
    'html': HtmlBuildsReport,
    'plain': PlainBuildsReport,
    'json': JSONBuildsReport,
}

_charmhub_report_klasses = {
    'html': HtmlCharmhubReport,
    'json': JSONCharmhubReport,
    'plain': PlainCharmhubReport,
}


def get_builds_report_klass(kind: str) -> BaseBuildsReport:
    """Get a report class for a kind of output.

    :param kind: type of output report.
    :returns: a report class.
    """
    return _build_report_klasses[kind]


def get_charmhub_report_klass(kind: str) -> BaseCharmhubReport:
    """Get a report class for a kind of output.

    :param kind: type of output report.
    :returns: a report class.
    """
    return _charmhub_report_klasses[kind]


def get_supported_report_types() -> List[str]:
    """Get list of support report types.

    :returns: list of supported report types.
    """
    return list(_build_report_klasses.keys())
