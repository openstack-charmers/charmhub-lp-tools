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

import argparse
import logging
import os
import pathlib
import sys

from typing import (
    Any,
    Dict,
    List,
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
    """Get the charm name defined in osci.yaml

    :param osci: the osci configuration set in the repo.
    :returns: the charm name
    """
    try:
        vars_ = get_project_vars(osci)
        return vars_['charm_build_name']
    except (TypeError, KeyError) as ex:
        logger.warning('charm_build_name not found in osci.yaml: %s' % ex)
        raise CharmNameNotFound()
    raise CharmNameNotFound()


def get_project_vars(osci: OsciYamlType) -> Dict[str, Any]:
    """Get 'vars' section from osci.yaml.

    :param osci: the osci configuration set in the repo.
    :returns: vars section
    """
    for section in osci:
        if 'project' not in section:
            continue

        return section['project']['vars']


def update_auto_build_channel_if_needed(auto_build_channels: Dict[str, Any],
                                        lp_key: str,
                                        project_vars: Dict[str, Any],
                                        osci_key: str,
                                        default: str = None):
    """Update the auto build channel if needed.

    :param auto_build_channels: dictionary with the auto build channels that
                                needs to be updated.
    :param lp_key: the name of the Launchpad auto build channel to update.
    :param project_vars: the project vars declared in osci.yaml
    :param osci_key: the name of the osci.yaml build channel to read the value
                     from.
    :param default: default value in case the osci_key is not present in
                    project_vars
    :returns: True if the auto_build_channels was updated, otherwise False.
    """
    new_value = project_vars.get(osci_key, default)
    if auto_build_channels.get(lp_key) != new_value:
        logger.info('Updating %s channel from %s to %s',
                    lp_key,
                    auto_build_channels.get(lp_key),
                    new_value)
        auto_build_channels[lp_key] = new_value
        return True
    else:
        logger.debug('auto build channel %s has not changed', lp_key)

    return False


def setup_parser(subparser: argparse.ArgumentParser):
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
    parser.set_defaults(func=main)
    return parser


def main(args: argparse.Namespace,
         gc: GroupConfig,
         ) -> None:
    logger.setLevel(getattr(logging, args.loglevel, 'ERROR'))
    git_repo = git.Repo(args.repo_dir, search_parent_directories=True)
    osci = load_osci_yaml(git_repo)
    branch_name = get_branch_name(git_repo)
    charm_name = get_charm_name(osci)
    project_vars = get_project_vars(osci)
    if not list(gc.projects(select=[charm_name])):
        logger.error("No charm '%s' found; is the name correct?", charm_name)
        sys.exit(1)

    charm_project = list(gc.projects(select=[charm_name]))[0]
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
        recipes = charm_project.lpt.get_charm_recipes(
            charm_project.lp_team,
            charm_project.lp_project)
        try:
            lp_recipe = [r for r in recipes if r.name == recipe_name][0]
            logger.info('Using recipe %s', lp_recipe.web_link)
        except IndexError:
            logger.error('Recipe %s not found in %s',
                         recipe_name, charm_project)
            sys.exit(2)

        auto_build_channels = lp_recipe.auto_build_channels
        lp_changed = False
        for lp_key, osci_key, default in LIST_AUTO_BUILD_CHANNELS:
            if update_auto_build_channel_if_needed(
                    auto_build_channels,
                    lp_key,
                    project_vars,
                    osci_key,
                    default):
                lp_changed = True

        if lp_changed:
            if args.i_really_mean_it:
                lp_recipe.auto_build_channels = auto_build_channels
                lp_recipe.lp_save()
                logger.info('LP recipe updated successfully.')
            else:
                logger.info('Dry-run mode: NOT committing the changes')
        else:
            logger.info('Nothing to change.')
