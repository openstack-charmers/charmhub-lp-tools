# Copyright 2023 Canonical

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Logic behince the ``ensure-series`` subcommand."""

import argparse
import logging
import sys

from .group_config import GroupConfig


logger = logging.getLogger(__name__)


def setup_parser(subparser: argparse.ArgumentParser):
    """Set up arguments parser for the CLI."""
    parser = subparser.add_parser(
        'ensure-series',
        help='Ensure series are present in Launchpad projects.',
    )
    parser.add_argument(
        '--i-really-mean-it',
        dest='i_really_mean_it',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the sync/apply '
              'command really should be used.'),
    )
    parser.add_argument(
        '-b', '--git-branch',
        dest="git_branches",
        action='append',
        metavar='GIT_BRANCH',
        type=str,
        help=('Git branch name to ensure the series for. Can be used multiple '
              'times.  If not included, then all branches for the charm '
              'will be processed.  If a charm doesn\'t have the branch then '
              'it will be ignored.'))
    parser.set_defaults(func=ensure_series)
    return parser


def ensure_series(args: argparse.Namespace,
                  gc: GroupConfig,
                  ) -> None:
    """Provide the main entry point for the ``ensure-series`` subcommand."""
    logger.setLevel(getattr(logging, args.loglevel, 'ERROR'))

    charm_projects = list(gc.projects(select=args.charms))
    if not charm_projects:
        logger.error("No charms were found that match the filter: %s",
                     args.charms)
        sys.exit(1)

    for cp in charm_projects:
        cp.ensure_series(branches=args.git_branches,
                         dry_run=not args.i_really_mean_it)
