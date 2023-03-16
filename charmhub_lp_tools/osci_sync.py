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
"""Logic behince the ``osci-sync`` subcommand."""

import argparse
import logging
import os
import pathlib
import pprint
import sys

from copy import deepcopy
from typing import (
    Any,
    Dict,
    List,
    Tuple,
)

import git
import yaml

from .charm_project import (
    DEFAULT_RECIPE_FORMAT,
)
from .constants import (
    LIST_AUTO_BUILD_CHANNELS,
    OSCI_YAML,
)
from .exceptions import (
    BranchNotFound,
    CharmNameNotFound,
    ProjectVarsNotFound,
)
from .gitutils import (
    get_branch_name,
)
from .group_config import GroupConfig
from .parsers import (
    parse_channel,
)


OsciYamlType = List[Any]
logger = logging.getLogger(__name__)


def diff_auto_build_channels(lp_auto_build_channels: Dict[str, str],
                             local_auto_build_channels: Dict[str, str]):
    """Obtain the differences between auto build channels maps.

    The differences are obtained using the symmetic difference operation from
    sets.

    :param lp_auto_build_channels: the baseline auto build channels as defined
                                   in Launchpad build recipe
    :param local_auto_build_channels: the auto build channels built from
                                      information defined in osci.yaml
    :returns: the differences between both auto build channels map.
    """
    a_set = set(lp_auto_build_channels.items())
    b_set = set(local_auto_build_channels.items())
    return dict(a_set ^ b_set)


def find_osci_yaml(git_repo: git.Repo) -> pathlib.Path:
    """Find the osci.yaml file path.

    :param git_repo: git repo of the charm
    :return: The path to the osci.yaml file.
    :raise FileNotFoundError: when no osci.yaml was found in the expected
                              location
    """
    git_root = git_repo.git.rev_parse('--show-toplevel')
    fpath = os.path.join(git_root, OSCI_YAML)
    if os.path.isfile(fpath):
        return pathlib.Path(fpath)
    else:
        raise FileNotFoundError(fpath)


def load_osci_yaml(git_repo: git.Repo) -> OsciYamlType:
    """Load osci.yaml content into memory.

    :param git_repo: git repo of the charm
    :return: parsed content of osci.yaml
    """
    fpath = find_osci_yaml(git_repo)
    with open(fpath, 'r') as f:
        return yaml.safe_load(f)


def get_charm_name(osci: OsciYamlType) -> str:
    """Get the charm name defined in osci.yaml.

    :param osci: the osci configuration set in the repo.
    :returns: the charm name
    :raises: CharmNameNotFound
    """
    try:
        vars_ = get_project_vars(osci)
        return vars_['charm_build_name']
    except ProjectVarsNotFound as ex:
        logger.warning('charm_build_name not found in osci.yaml: %s' % ex)
        raise CharmNameNotFound()


def get_project_vars(osci: OsciYamlType) -> Dict[str, Any]:
    """Get 'vars' section from osci.yaml.

    :param osci: the osci configuration set in the repo.
    :returns: vars section
    :raises: ProjectVarsNotFound
    """
    for section in osci:
        try:
            return section['project']['vars']
        except KeyError:
            pass
    else:
        raise ProjectVarsNotFound()


def gen_auto_build_channel(auto_build_channels: Dict[str, Any],
                           project_vars: Dict[str, Any],
                           auto_build_keys: List[Tuple[str, str, object]],
                           ) -> Dict[str, str]:
    """Generate a auto_build_channel compatible map.

    Generate a auto_build_channel map based on the information available in
    ``project_vars`` for the value referenced by ``osci_key``.

    :param auto_build_channels: dictionary with the auto build channels to
                                compare against.
    :param project_vars: the project vars declared in osci.yaml
    :param auto_build_keys: list of tuples where each tuple contains the key
                            that should be accessed from the
                            auto_build_channels map, the key that should be
                            accessed from the project_vars map and finally a
                            default value if the key is not found in
                            project_vars.
    :returns: A new dictionary with the updated values.

    """
    new_auto_build_channels = deepcopy(auto_build_channels)
    for lp_key, osci_key, default in auto_build_keys:
        new_value = project_vars.get(osci_key, default)
        if auto_build_channels.get(lp_key) != new_value:
            logger.debug('%s channel from %s to %s',
                         lp_key,
                         auto_build_channels.get(lp_key),
                         new_value)
            new_auto_build_channels[lp_key] = new_value

    return new_auto_build_channels


def setup_parser(subparser: argparse.ArgumentParser):
    """Set up arguments parser for the CLI."""
    parser = subparser.add_parser(
        'osci-sync',
        help='Sync the config defined in osci.yaml to Launchpad.',
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
        '--repo-dir',
        dest='repo_dir',
        default=os.getcwd(),
        metavar='DIR',
        help=('Path to the git repository where the charm is located at, by '
              'default it uses the current working directory.'),
    )
    parser.set_defaults(func=osci_sync)
    return parser


def osci_sync(args: argparse.Namespace,
              gc: GroupConfig,
              ) -> None:
    """Provide the main entry point for the ``osci-sync`` subcommand."""
    logger.setLevel(getattr(logging, args.loglevel, 'ERROR'))
    git_repo = git.Repo(args.repo_dir, search_parent_directories=True)
    osci = load_osci_yaml(git_repo)
    branch_name = get_branch_name(git_repo)
    charm_name = get_charm_name(osci)
    project_vars = get_project_vars(osci)
    try:
        charm_project = list(gc.projects(select=[charm_name]))[0]
    except IndexError:
        logger.error("No charm '%s' found; is the name correct?", charm_name)
        sys.exit(1)

    try:
        branch = charm_project.branches[f'refs/heads/{branch_name}']
    except KeyError:
        logger.error("The branch '%s' was not found in %s",
                     branch_name, charm_project)
        raise BranchNotFound(charm_project, branch_name)

    logger.debug('channels: %s', branch['channels'])
    for channel in branch['channels']:
        (track, risk) = parse_channel(channel)
        recipe_name = DEFAULT_RECIPE_FORMAT.format(
            project=charm_project.lp_project.name,
            branch=branch_name.replace('/', '-'),
            track=track)
        try:
            lp_recipe = charm_project.lp_recipes[recipe_name]
            logger.info('Using recipe %s', lp_recipe.web_link)
        except KeyError:
            logger.error('Recipe %s not found in %s',
                         recipe_name, charm_project)
            # TODO(freyes): handle recipe creation when the recipe was not
            # found, this would greatly enhance the developer experience when
            # cutting new stable releases.
            sys.exit(2)

        auto_build_channels = lp_recipe.auto_build_channels
        new_auto_build_channels = gen_auto_build_channel(
            auto_build_channels, project_vars, LIST_AUTO_BUILD_CHANNELS)
        diff = diff_auto_build_channels(auto_build_channels,
                                        new_auto_build_channels)
        if diff:
            logger.info('The auto build channels have changed: %s',
                        pprint.pformat(diff))
            if args.i_really_mean_it:
                lp_recipe.auto_build_channels = new_auto_build_channels
                lp_recipe.lp_save()
                logger.info('LP recipe updated successfully.')
            else:
                logger.info('Dry-run mode: NOT committing the changes')
        else:
            logger.info('Nothing to change.')
