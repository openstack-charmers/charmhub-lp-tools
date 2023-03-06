# Copyright 2021 Canonical

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Tools to configure and manage repositories and launchpad builders.

This file contains a command that provides the ability to configure and manage
the launchpad builders, repositories and branches in repositories.

The commands are:
   show -> display the current config.
   list -> show a list of the charms configured in the supplied config.
   diff -> show the current config and a diff to what is asked for.
   config -> show the asked for config
   sync -> sync the asked for config to the charm in the form of recipes.

Note that 'sync' requires the --i-really-mean-this flag as it is potentially
destructive.  'sync' also has other flags.

As always, use the -h|--help on the command to discover what the options are
and how to manage it.

Note: the script will attempt to read a config file at
$XDG_CONFIG_HOME/charmhub_lp_tools/charmhub_lp_tools.conf.  If $XDG_CONFIG_HOME
is not set, the $HOME/.config/charmhub_lp_tools/charmhub_lp_tools.conf will be
looked for.  If either of these files exist then the following keys are read
from them:

config_dir = the directory that the config.yaml files are held.
log_level  = (ERROR, DEBUG, WARNING, INFO, or unset)
ignore_errors = true|false (false is the default)
"""

import argparse
import collections
import collections.abc
import logging
import os
import pathlib
import pprint
import sys
import yaml

from datetime import datetime
from typing import (Any, Dict, Iterator, List, Optional, NamedTuple)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from .launchpadtools import (
    LaunchpadTools,
    setup_logging as lpt_setup_logging,
)
from .charm_project import (
    CharmChannel,
    CharmProject,
    setup_logging as cp_setup_logging,
)
from .charmhub import setup_logging as ch_setup_logging
from .reports import (
    get_builds_report_klass,
    get_charmhub_report_klass,
    get_supported_report_types,
)
from .exceptions import InvalidRiskLevel
from .schema import config_schema


logger = logging.getLogger(__name__)

LOGGING_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
NOW = datetime.now(tz=ZoneInfo("UTC"))


class FileConfig(NamedTuple):
    config_dir: Optional[str] = None
    log_level: Optional[str] = None
    ignore_errors: bool = False


def _read_config_file() -> Dict[str, Any]:
    """Read the config file, if it exists, and return any set items."""
    if os.environ.get('XDG_CONFIG_HOME'):
        root = pathlib.Path(os.environ['XDG_CONFIG_HOME'])
    elif os.environ.get('HOME'):
        root = pathlib.Path(os.environ['HOME'])
    else:
        return {}
    config_file = root / '.config' / 'charmhub_lp_tools' / 'config.yaml'
    if config_file.is_file():
        try:
            with config_file.open() as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error("Couldn't read %s: %s", str(config_file), str(e))
    return {}


def get_file_config() -> FileConfig:
    """Return default config, if any, from a config file location."""
    config_items = _read_config_file()
    log_level = str(config_items.get('log_level', '')).upper()
    if log_level not in ('', 'ERROR', 'DEBUG', 'WARNING', 'INFO'):
        log_level = None
    log_level = log_level or None
    return FileConfig(
        config_dir=config_items.get('config_dir'),
        log_level=log_level,
        ignore_errors=bool(config_items.get('ignore_errors', False)))


def check_config_dir_exists(dir_: pathlib.Path) -> pathlib.Path:
    """Validate that the config dir_ exists.

    Raises FileNotFoundError if it doesn't.

    :param dir_: the config path that needs to exist.
    :raises: FileNotFoundError if the configuration directory doesn't exist.
    """
    if not dir_.exists():
        raise FileNotFoundError(
            f'Configuration directory "{dir_}" does not exist')
    return dir_


def get_group_config_filenames(config_dir: pathlib.Path,
                               project_group_names: Optional[List[str]] = None,
                               extension: str = ".yaml",
                               ) -> List[pathlib.Path]:
    """Fetch the list of files for the group config.

    Depending on whether :param:`project_group_names` is passed, get the list
    of files that contain the projects that need configuring.

    :param config_dir: the directory to look in
    :param project_group_names: Optional list of names to filter on.
    :param extension: the extension (default '.yaml') to use for the
        project_group_names
    :returns: the list of paths corresponding to the files.
    :raises: FileNotFoundError if a name.extension in the config_dir doesn't
        exist.
    """
    # Load the various project group configurations
    if not project_group_names:
        files = list(config_dir.glob(f'*{extension}'))
    else:
        files = [config_dir / f'{group}{extension}'
                 for group in project_group_names]
        # validate that the files actually exist
        for file in files:
            if not (file.exists()):
                raise FileNotFoundError(
                    f"The group config file '{file}' wasn't found")
    return files


class GroupConfig:
    """Collect together all the config files and build CharmProject objects.

    This collects together the files passed (which define a charm projects
    config and creates CharmProject objects to ensure git repositories and
    ensure that the charm builder recipes in launchpad exist with the correct
    settings.
    """

    def __init__(self,
                 lpt: 'LaunchpadTools',
                 files: List[pathlib.Path] = None) -> None:
        """Configure the GroupConfig object.

        :param files: the list of files to load config from.
        """
        self.lpt = lpt
        self.charm_projects: Dict[str, 'CharmProject'] = (
            collections.OrderedDict())
        if files is not None:
            self.load_files(files)

    def load_files(self, files: List[pathlib.Path] = None) -> None:
        """Load the files into the object.

        This loads the files, and configures the projects and then creates
        CharmProject objects.

        :param files: the list of files to load config from.
        """
        assert not (isinstance(files, str)), "param files must not be str"
        assert isinstance(files, collections.abc.Sequence), \
            "Must pass a list or tuple."
        for file in files:
            with open(file, 'r') as f:
                group_config = yaml.safe_load(f)
                # validate the content against the schema
                config_schema.validate(group_config)
            logger.debug('group_config is: \n%s', pprint.pformat(group_config))
            project_defaults = group_config.get('defaults', {})
            # foo/bar/openstack.yaml -> openstack
            project_group = os.path.splitext(os.path.basename(file))[0]
            for project in group_config.get('projects', []):
                for key, value in project_defaults.items():
                    project.setdefault(key, value)
                logger.debug('Loaded project %s', project.get('name'))
                project['project_group'] = project_group
                self.add_charm_project(project)

    def add_charm_project(self,
                          project_config: Dict[str, Any],
                          merge: bool = False,
                          ) -> None:
        """Add a CharmProject object from the project specification dict.

        :param project: the project to add.
        :param merge: if merge is True, merge/overwrite the existing object.
        :raises: ValueError if merge is false and the charm project already
            exists.
        """
        name: str = project_config.get('name')  # type: ignore
        if name in self.charm_projects:
            if merge:
                self.charm_projects[name].merge(project_config)
            else:
                raise ValueError(
                    f"Project config for '{name}' already exists.")
        else:
            self.charm_projects[name] = CharmProject(project_config, self.lpt)

    def projects(self, select: Optional[List[str]] = None,
                 ) -> Iterator[CharmProject]:
        """Generator returns a list of projects."""
        if not (select):
            select = None
        for project in self.charm_projects.values():
            if (select is None or
                    project.launchpad_project in select or
                    project.charmhub_name in select):
                yield project


def parse_args(argv: Optional[List[str]],
               config_from_file: FileConfig) -> argparse.Namespace:
    """Parse the arguments and return the parsed args.

    Work out what command is being run and collect the arguments
    associated with it.

    :param argv: The list of command line arguments to parse.
    :param config_from_file: the arguments from the config_file, if any
    :returns: parsed arguments
    """
    parser = argparse.ArgumentParser(
        description='Configure launchpad projects for charms'
    )
    default_config_dir = config_from_file.config_dir or os.getcwd()
    default_log_level = config_from_file.log_level or 'ERROR'
    default_ignore_errors = config_from_file.ignore_errors
    parser.add_argument('--config-dir',
                        type=str, default=default_config_dir,
                        help=('directory containing configuration files. '
                              'The default is the current working directory.'))
    parser.add_argument('--log', dest='loglevel',
                        type=str.upper,
                        default=default_log_level,
                        choices=('DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'),
                        help='Loglevel')
    parser.add_argument('-p', '--group',
                        dest='project_groups',
                        action='append',
                        metavar='PROJECT-GROUP',
                        # type=str, nargs='*',
                        type=str,
                        help='Project group configurations to process. If no '
                             'project groups are specified, all project '
                             'groups found in the config-dir will be loaded '
                             'and processed.')
    parser.add_argument('-c', '--charm',
                        dest='charms',
                        action='append',
                        metavar='CHARM',
                        type=str,
                        help=('Choose a specific charm name from the '
                              'configured set. May be repeated for multiple '
                              'charms.'))
    parser.add_argument('-f', '--format',
                        dest='format',
                        metavar='FORMAT',
                        type=str,
                        choices=get_supported_report_types(),
                        default='plain',
                        help='Specify the output format')
    parser.add_argument('-i', '--ignore-errors',
                        dest='ignore_errors',
                        default=default_ignore_errors,
                        action='store_true',
                        help='Ignore errors and try to carry on.')
    parser.add_argument('--anonymous', dest='anonymous', action='store_true',
                        help='Login anonymously to Launchpad.')

    subparser = parser.add_subparsers(required=True, dest='cmd')
    show_command = subparser.add_parser(
        'show',
        help=('The "show" command shows the current configuration for the '
              'charm recipes as defined in launchpad.'))
    show_command.set_defaults(func=show_main)
    list_command = subparser.add_parser(
        'list',
        help='List the charms defined in the configuration passed.')
    list_command.set_defaults(func=list_main)
    diff_command = subparser.add_parser(
        'diff',
        help=('Diff the declared config with the actual config in launchpad. '
              'This shows the config and highlights missing or extra '
              'configuration that is in launchpad. Note that git repositories '
              'can have extra branches and these are not seen in the diff. '
              'Missing branches that are in the config are highlighted.'))
    diff_command.set_defaults(func=diff_main)
    diff_command.add_argument('--detail',
                              action='store_true',
                              default=False,
                              help="Add detail to the output.")
    sync_command = subparser.add_parser(
        'sync',
        help=('Sync the config to launchpad. Effectively, this takes the diff '
              'and applies it to the projects, creating or updating recipes '
              'as required.'))
    sync_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the sync/apply '
              'command really should be used.'))
    sync_command.add_argument(
        '--remove-unknown',
        dest='remove_unknown_recipes',
        action='store_true',
        default=False,
        help=('If set, this flag indicates that any recipes that are not in '
              'the config for a charm will be deleted. This is so that '
              'recipes can be renamed and moved about and not leave behind '
              'recipes that both try to write to the target track.'))
    sync_command.add_argument(
        '--git-mirror-only',
        dest='git_mirror_only',
        action='store_true',
        default=False,
        help=('Use this flag to indicate to only setup the git mirroring and'
              'not set-up the recipes.'))
    sync_command.add_argument(
        '-b', '--git-branch',
        dest="git_branches",
        action='append',
        metavar='GIT_BRANCH',
        type=str,
        help=('Git branch name to sync recipe for.  Can be used multiple '
              'times.  If not included, then all branches for the charm '
              'will be synced.  If a charm doesn\'t have the branch then '
              'it will be ignored.'))
    sync_command.set_defaults(func=sync_main)
    # Delete recipes
    delete_command = subparser.add_parser(
        'delete',
        help=("Delete a recipe from launchpad based on a track/risk. e.g. "
              "use --track latest --risk edge to remove the recipe that "
              "pushes to the latest/stable track.  Note it does not remove "
              "the revision from the charmhub.  This is purely managing the "
              "recipes in launchpad."))
    group = delete_command.add_mutually_exclusive_group(required=False)
    track_branch_group = group.add_argument_group()
    track_branch_group.add_argument(
        '--track', '-t',
        dest='track',
        help=('The track to target. e.g. latest'))
    track_branch_group.add_argument(
        '--git-branch', '-b',
        dest='branch',
        help=('The branch to target. e.g. stable/xena'))
    group.add_argument(
        '--name',
        dest='recipe_name',
        help=('Name the recipe fully that you want to delete.'))
    delete_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the delete recipe '
              'command really should be used.'))
    delete_command.set_defaults(func=delete_main)
    # check-builds
    check_builds_commands = subparser.add_parser(
        'check-builds',
        help='Check the state of the builds available at Launchpad.')
    check_builds_commands.add_argument(
        '--arch',
        dest='arch_tag',
        help='Filter builds by architecture tag (e.g. arm64)')
    check_builds_commands.add_argument(
        '--detect-error',
        dest='detect_error',
        action='store_true',
        help='Look for the ERROR in the build log.')
    check_builds_commands.add_argument(
        '--channel',
        dest='channels',
        action='append',
        metavar='CHANNEL',
        help=('Filter the builds by channel (e.g. latest/edge). May be '
              'repeated for multiple channels.'),
    )
    check_builds_commands.add_argument(
        '-o', '--output', metavar='OUTPUT', dest='output',
        default='./report',
        help='Write report to OUTPUT.'
    )
    check_builds_commands.set_defaults(func=check_builds_main)
    # authorize helper
    authorize_command = subparser.add_parser(
        'authorize',
        help=("Authorize helper to authorize the launchpad recipes to upload "
              "to the charmhub.  Each recipe needs authorization, and this "
              "helper will use the same filters used to select the project "
              "group, charms, ignored charms, and branch to select the charm "
              "recipes that need authorizing. The Charmhub user that can "
              "upload charms will need to be logged in. This is a different "
              "user account than Launchpad."))
    authorize_command.add_argument(
        '-b', '--git-branch',
        dest="git_branches",
        action='append',
        metavar='GIT_BRANCH',
        type=str,
        help=('Git branch name to authorize recipes for.  Can be used '
              'multiple times.  If not included, then all branches for the '
              'charm will be authorized.  If a charm doesn\'t have the branch '
              'then it will be ignored.'))
    authorize_command.add_argument(
        '--force',
        dest='force',
        action='store_true',
        help=('Force an authorization even if Launchpad holds authorization '
              'for the recipe. This can be used to force a new authorization, '
              'or to change which account is authorizing the recipe.'))
    authorize_command.set_defaults(func=authorize_main)

    # request-build helper
    request_build_command = subparser.add_parser(
        'request-build',
        help=('Request the building of recipes on Launchpad, a check is made '
              'on the client side to determine if a new build is really '
              'needed, unless --force is passed.')
    )
    request_build_command.add_argument(
        '--force',
        dest='force',
        action='store_true',
        help='Force requesting a new build.'
    )
    request_build_command.add_argument(
        '-b', '--git-branch',
        dest="git_branches",
        action='append',
        metavar='GIT_BRANCH',
        type=str,
        help=('Git branch name to filter the recipes that will be requested '
              'to be built.  Can be used multiple times.  If not included, '
              'then all branches for the charm will be attempted to be built. '
              'If a charm doesn\'t have the branch then it will be ignored.')
    )
    request_build_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the request-build '
              'should really submit the requests to Launchpad.')
    )
    request_build_command.set_defaults(func=request_build)

    # request-code-import helper
    request_code_import_command = subparser.add_parser(
        'request-code-import',
        help='Request a new code import on Launchpad'
    )
    request_code_import_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really submit the requests to Launchpad.')
    )
    request_code_import_command.set_defaults(func=request_code_import)

    # copy-channel
    copy_channel_command = subparser.add_parser(
        'copy-channel',
        help=('Copy all the charms available in a channel (track/risk) to '
              'another channel'),
    )
    copy_channel_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really commit the changes.')
    )
    copy_channel_command.add_argument(
        '-s', '--source', dest='src_channel',
        metavar='CHANNEL', required=True,
        help='Source channel to copy charms from.'
    )
    copy_channel_command.add_argument(
        '-d', '--destination', dest='dst_channel',
        metavar='CHANNEL', required=True,
        help='Destination channel to copy charms to.'
    )
    copy_channel_command.add_argument(
        '--close-channel-before',
        dest='close_dst_channel_before',
        action='store_true',
        default=False,
        help=('Close the destination channel before copying the new charms '
              'to it.'),
    )
    copy_channel_command.add_argument(
        '--base',
        dest='bases',
        action='append',
        metavar='BASE',
        required=True,
        type=str,
        help=('Select charm(s) that run on the base (e.g. 20.04, 22.04). '
              'Can be used multiple times.')
    )
    copy_channel_command.add_argument(
        '--ignore-arch',
        dest='ignore_arches',
        action='append',
        metavar='IGNORE_ARCH',
        type=str,
        help='Architecture to ignore/not copy; repeat for multiple arches'
    )
    copy_channel_command.add_argument(
        '--force',
        dest='force',
        action='store_true',
        help='Force copying revision into channel.'
    )
    copy_channel_command.add_argument(
        '--retries', metavar='N',
        dest='retries',
        type=int,
        default=3,
        help='Retry calls when charmhub issues a 504 error',
    )
    copy_channel_command.set_defaults(func=copy_channel)
    # charmhub-report
    ch_report_commands = subparser.add_parser(
        'charmhub-report',
        help='Generate a report based on the published charms in Charmhub.')
    ch_report_commands.add_argument(
        '-t', '--track',
        dest='tracks',
        action='append',
        metavar='TRACK',
        required=True,
        help='Select only tracks that match TRACK',
    )
    ch_report_commands.add_argument(
        '-o', '--output', metavar='OUTPUT', dest='output',
        default=None,
        help='Write report to OUTPUT.'
    )
    ch_report_commands.set_defaults(func=ch_report_main)

    # clean-channel
    clean_channel_command = subparser.add_parser(
        'clean-channel',
        help=('Clean a channel (track/risk) by specifying the base(s) to '
              'keep. This will find the relevant revisions for those bases, '
              'then close the channel, and finally re-release the revisions '
              'back to that channel. NOTE: this is quite dangerous - use '
              'sparingly!.'),
    )
    clean_channel_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really commit the changes.')
    )
    clean_channel_command.add_argument(
        '-s', '--source', dest='src_channel',
        metavar='CHANNEL', required=True,
        help='The channel to clean.',
    )
    clean_channel_command.add_argument(
        '--base',
        dest='bases',
        action='append',
        metavar='BASE',
        required=True,
        type=str,
        help=('Select charm(s) that run on the base (e.g. 20.04, 22.04). '
              'Can be used multiple times.')
    )
    clean_channel_command.add_argument(
        '--ignore-arch',
        dest='ignore_arches',
        action='append',
        metavar='IGNORE_ARCH',
        type=str,
        help='Architecture to ignore/not copy; repeat for multiple arches'
    )
    clean_channel_command.add_argument(
        '--retries', metavar='N',
        dest='retries',
        type=int,
        default=3,
        help='Retry calls when charmhub issues a 504 error',
    )
    clean_channel_command.set_defaults(func=clean_channel)

    # close-channel
    close_channel_command = subparser.add_parser(
        'close-channel',
        help=('Close a channel (track/risk) by specifying channel. '
              'This will check that the revision is not unique and is '
              'available on another risk for that track.  If the --force '
              'flag is used, the check for uniqueness is not made. '
              '--i-really-mean-it is needed, or --interactive, to actually '
              'make the change. NOTE: this is quite dangerous - use '
              'sparingly!.'),
    )
    close_channel_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really commit the changes.')
    )
    close_channel_command.add_argument(
        '--force',
        dest='force',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that a channel with a '
              'unique revision in it (from all the revisions) will be okay '
              'to be closed.')
    )
    close_channel_command.add_argument(
        '-s', '--channel', dest='channel',
        metavar='CHANNEL', required=True,
        help='The channel as track/risk to clean.',
    )
    close_channel_command.add_argument(
        '--retries', metavar='N',
        dest='retries',
        type=int,
        default=3,
        help='Retry calls when charmhub issues a 504 error',
    )
    close_channel_command.set_defaults(func=close_channel)

    # promote
    copy_revisions_command = subparser.add_parser(
        'copy-revision',
        help=('Copy a revisions on a track from one risk to a new risk.  '
              'This works by the track name.  If the track (via the branch) '
              'is configured as a duplicate-track, then those tracks are '
              'also duplicated using the same revision.  The target track '
              'is optionally cleaned using the bases configured via the '
              'associated git branch.  If the configuration is broken, and '
              'a unique git branch is not ' 'associated, then the command '
              'will fail.'),
    )
    copy_revisions_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really commit the changes.')
    )
    copy_revisions_command.add_argument(
        '-t', '--track', dest='track',
        metavar='TRACK', required=True,
        help='The track on which to promote.',
    )
    copy_revisions_command.add_argument(
        '-f', '--from-risk',
        dest='from_risk',
        metavar='FROM-RISK',
        required=True,
        type=str.lower,
        choices=('edge', 'beta', 'candidate', 'stable'),
        help='The risk to promote from.',
    )
    copy_revisions_command.add_argument(
        '-g', '--to-risk',
        dest='to_risk',
        metavar='FROM-RISK',
        required=True,
        type=str.lower,
        choices=('edge', 'beta', 'candidate', 'stable'),
        help='The risk to promote to.',
    )
    copy_revisions_command.add_argument(
        '--retries', metavar='N',
        dest='retries',
        type=int,
        default=3,
        help='Retry calls when charmhub issues a 504 error',
    )
    copy_revisions_command.set_defaults(func=copy_revisions)

    # repair resources
    # Repair releases on a charm, channel, base filter by finding the
    # revisions, identifying the metadata for that git branch and then finding
    # resources to match (by highest number) and re-releasing the charm to the
    # channel.  This is to fix existing channels that had a charm released
    # without resources.
    repair_resource_command = subparser.add_parser(
        'repair-resources',
        help=("Repair releases on charmhub, filtering by charm, channel and "
              "bases - all of which are optional.  The revision is found, "
              "matched to the git branch and then the associated resources "
              "determined from the metadata for that branch.  This may be "
              "wrong, so use this command sparingly when you know that it "
              "will make the correct decision."),
    )
    repair_resource_command.add_argument(
        '--i-really-mean-it',
        dest='confirmed',
        action='store_true',
        default=False,
        help=('This flag must be supplied to indicate that the operation '
              'should really commit the changes.')
    )
    repair_resource_command.add_argument(
        '-s', '--channel', dest='channel',
        metavar='CHANNEL', required=True,
        help='The optional channel to repair resources on.',
    )
    repair_resource_command.add_argument(
        '--base',
        dest='bases',
        action='append',
        metavar='BASE',
        required=False,
        type=str,
        help=('Select revision(s) that run on the base (e.g. 20.04, 22.04). '
              'Can be used multiple times.')
    )
    repair_resource_command.add_argument(
        '--retries', metavar='N',
        dest='retries',
        type=int,
        default=3,
        help='Retry calls when charmhub issues a 504 error',
    )
    repair_resource_command.set_defaults(func=repair_resource)

    validate_config_commands = subparser.add_parser(
        'validate-config',
        help='Validate the configuration according to the schema.')
    validate_config_commands.set_defaults(func=validate_config_main)

    # finally, parse the args and return them.
    args = parser.parse_args(argv)
    return args


def show_main(args: argparse.Namespace,
              gc: GroupConfig,
              ) -> None:
    """Show a the charm config in launchpad, if any for the group config.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        cp.show_lauchpad_config()


def list_main(args: argparse.Namespace,
              gc: GroupConfig,
              ) -> None:
    """List the charm projects (and repos) that are in the configuration.

    This simply lists the charm projects in the GlobalConfig.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    def _heading():
        print(f"{'-'*20} {'-'*30} {'-'*40} {'-'*len('Repository')}")
        print(f"{'Team':20} {'Charmhub name':30} {'LP Project Name':40} "
              f"{'Repository'}")
        print(f"{'-'*20} {'-'*30} {'-'*40} {'-'*len('Repository')}")

    for i, cp in enumerate(gc.projects(select=args.charms)):
        if i % 30 == 0:
            _heading()
        print(f"{cp.team:20} {cp.charmhub_name[:30]:30} "
              f"{cp.launchpad_project[:40]:40} {cp.repository}")


def diff_main(args: argparse.Namespace,
              gc: GroupConfig,
              ) -> None:
    """Show a diff between the requested LP config and current config.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        cp.print_diff(args.detail)


def sync_main(args: argparse.Namespace,
              gc: GroupConfig,
              ) -> None:
    """Do the sync from the config to the projects defined in config.

    This takes the GroupConfig and then ensures that the git repository is set
    up in launchpad for each project, and then ensures that the required charm
    recipes are sdet up for that project in launchpad.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    if not args.confirmed:
        print("--i-really-mean-it flag not used so this is dry run only.")
    if args.git_mirror_only:
        logger.info("Only ensuring mirroring of git repositories.")
    for charm_project in gc.projects(select=args.charms):
        charm_project.ensure_git_repository(dry_run=not (args.confirmed))
        if not (args.git_mirror_only):
            charm_project.ensure_charm_recipes(
                args.git_branches,
                remove_unknown=args.remove_unknown_recipes,
                dry_run=not (args.confirmed))
        print()


def delete_main(args: argparse.Namespace,
                gc: GroupConfig,
                ) -> None:
    """Delete a recipe determined by name of track/risk for charms selected.

    This uses the GroupConfig and then deletes the recipe associated with the
    track/risk, or just name, for that GroupConfig item if it exists.  If it
    doesn't then a warning is logged.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    if not args.confirmed:
        print("--i-really-mean-it flag not used so this is dry run only.")
    if not args.recipe_name:
        if not (args.track) and not (args.branch):
            raise AssertionError(
                "'delete' command: must supply either (track and branch) or "
                "name parameters.  See --help for command.")
    for charm_project in gc.projects(select=args.charms):
        try:
            if args.recipe_name:
                charm_project.delete_recipe_by_name(
                    recipe_name=args.recipe_name,
                    dry_run=not (args.confirmed))
            else:
                charm_project.delete_recipe_by_branch_and_track(
                    track=args.track,
                    branch=args.branch,
                    dry_run=not (args.confirmed))
        except KeyError as e:
            logger.warning("Delete failed as recipe not found: charm: %s "
                           " reason: %s", charm_project.name, str(e))
            if not args.ignore_errors:
                raise
        except Exception as e:
            logger.warning("Error deleting recipe: charm: %s, reason: %s",
                           charm_project.charmhub_name, str(e))
            if not args.ignore_errors:
                raise
        print()


def check_builds_main(args: argparse.Namespace,
                      gc: GroupConfig,
                      ) -> None:
    """Check the state of the builds in Launchpad.

    :param args: the arguments parsed from the command line.
    :param gc: The GroupConfig; i.e. all the charms and their config.
    """
    klass = get_builds_report_klass(args.format)
    build_report = klass(args.output)
    for cp in gc.projects(select=args.charms):
        for (recipe, build) in cp.get_builds(set(args.channels), args.arch_tag,
                                             args.detect_error):
            build_report.add_build(cp, recipe, build)

    build_report.generate()


def authorize_main(args: argparse.Namespace,
                   gc: GroupConfig,
                   ) -> None:
    """Authorize a set of recipes to be uploaded to charmhub.

    This needs to be done on a machine where a browser can be launched to
    perform the login (to get the macaroon) for charmhub.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        cp.authorize(args.git_branches, args.force)


def request_build(args: argparse.Namespace,
                  gc: GroupConfig,
                  ) -> None:
    """Request a build on Launchpad.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        cp.request_build_by_branch(args.git_branches, args.force,
                                   dry_run=not args.confirmed)


def request_code_import(args: argparse.Namespace,
                        gc: GroupConfig,
                        ) -> None:
    """Request a code import on Launchpad.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        cp.request_code_import(dry_run=not args.confirmed)
        print(f'Requested import of {cp}')


def copy_channel(args: argparse.Namespace,
                 gc: GroupConfig,
                 ) -> Dict[str, List[int]]:
    """Copy the charms released from a channel to another one.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    :returns: a dictionary of charm name -> revisions released.
    """
    cp_revs = {}
    for cp in gc.projects(select=args.charms):
        src_channel = CharmChannel(cp, args.src_channel)
        dst_channel = CharmChannel(cp, args.dst_channel)

        if src_channel.track not in cp.charmhub_tracks:
            logger.error('%s not in %s',
                         str(src_channel.track),
                         cp.charmhub_tracks)
            continue

        if dst_channel.track not in cp.charmhub_tracks:
            logger.error('%s not in %s',
                         str(dst_channel.track),
                         cp.charmhub_tracks)
            continue

        if args.close_dst_channel_before:
            logger.info('Closing %s: %s', cp.charmhub_name, dst_channel.name)
            dst_channel.close(dry_run=not args.confirmed,
                              retries=args.retries)

        logger.info('Copying charm %s from %s to %s for bases: %s',
                    cp.charmhub_name,
                    src_channel.name,
                    dst_channel.name,
                    ', '.join(args.bases))
        revs = cp.copy_channel(src_channel,
                               dst_channel,
                               bases=args.bases,
                               ignore_arches=args.ignore_arches,
                               dry_run=not args.confirmed,
                               force=args.force,
                               retries=args.retries)
        cp_revs[cp.charmhub_name] = revs
    return cp_revs


def ch_report_main(args: argparse.Namespace,
                   gc: GroupConfig,
                   ):
    """Generate report of published charms.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    klass = get_charmhub_report_klass(args.format)
    report = klass(args.output)
    for track in args.tracks:
        for cp in gc.projects(select=args.charms):
            # if the charm doesn't declare its use of the track, then we skip
            # it.
            if track not in cp.tracks:
                logger.debug('Skipping %s since it does not use track %s',
                             cp.charmhub_name, track)
                continue
            for risk in ['edge', 'beta', 'candidate', 'stable']:
                channel = CharmChannel(cp, f'{track}/{risk}')
                # get all the revisions, without filtering by base nor arch.
                revs_found = 0
                for channel_def in channel.channel_map:
                    if (channel_def['channel']['track'],
                            channel_def['channel']['risk']) == (track, risk):
                        report.add_revision(channel, channel_def)
                        revs_found += 1
                if revs_found == 0:
                    # add a fake revision to force the inclusion in the list
                    # of charms of the final report.
                    report.add_revision(channel, {})
                logger.info('Found %s charm revisions for %s in %s/%s',
                            revs_found, cp.charmhub_name, track, risk)

    report.generate()


def clean_channel(args: argparse.Namespace,
                  gc: GroupConfig,
                  ) -> None:
    """Clean a channel by keeping charm revisions specified by bases.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        src_channel = CharmChannel(cp, args.src_channel)

        logger.info('Cleaing channel "%s", for charm %s for bases: %s',
                    src_channel.name,
                    cp.charmhub_name,
                    ', '.join(args.bases))
        cp.clean_channel(src_channel,
                         bases=args.bases,
                         ignore_arches=args.ignore_arches,
                         dry_run=not args.confirmed,
                         retries=args.retries)


def copy_revisions(args: argparse.Namespace,
                   gc: GroupConfig,
                   ) -> None:
    """Promote a track by copying revisions between risk levels.

    Promoting a track is basically selecting the correct revisions between two
    risk levels.  The "correct" revisions are selected based on the bases
    configured for the branch as determined from the track.  If the
    configuration is malformed and a single branch can't be identified uniquely
    then the command will fail:

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    assert args.from_risk != args.to_risk, "Can't copy from/to same risk"
    for cp in gc.projects(select=args.charms):
        channel_def = f"{args.track}/{args.from_risk}"
        src_channel = CharmChannel(cp, channel_def)
        logger.info('Copying revision charm "%s" on track "%s" from risk "%s" '
                    'to "%s"',
                    cp.charmhub_name, args.track, args.from_risk, args.to_risk)
        try:
            cp.copy_revisions(channel=src_channel,
                              to_risk=args.to_risk,
                              dry_run=not args.confirmed,
                              retries=args.retries)
        except Exception as e:
            logger.info("Couldn't copy revision for: %s", cp.charmhub_name)
            logger.error("Exception %s", str(e))


def close_channel(args: argparse.Namespace,
                  gc: GroupConfig,
                  ) -> None:
    """Close a channel.

    Close a channel.  This might be done to clean-up a particular risk
    (although note that there is a clean-channel command for that purpose).

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        try:
            src_channel = CharmChannel(cp, args.channel)
        except InvalidRiskLevel:
            print(f"Channel {args.channel} doesn't have a valid risk")
            return
        logger.info('Closing channel "%s" for charm: %s',
                    args.channel, cp.charmhub_name)
        try:
            cp.close_channel(channel=src_channel,
                             dry_run=not args.confirmed,
                             force=args.force,
                             retries=args.retries)
        except Exception as e:
            logger.info("Couldn't close channel for: %s", cp.charmhub_name)
            logger.error("Exception: %s", str(e))


def repair_resource(args: argparse.Namespace,
                    gc: GroupConfig,
                    ) -> None:
    """Repair resources on revisions released to channels.

    This is done by re-releasing the charm revision according to the metadata
    for that channel obtained from the git repository.  Obviously, if a git
    repo branch can't be determined then the revision can't be re-released; in
    that case it'll need to be done manually.

    :param args: the arguments parsed from the command line.
    :para gc: The GroupConfig; i.e. all the charms and their config.
    """
    for cp in gc.projects(select=args.charms):
        if '/' in args.channel:
            channels = [args.channel]
        else:
            channels = [f"{args.channel}/{r}"
                        for r in ('stable', 'candidate', 'beta', 'edge')]
        for channel in channels:
            src_channel = CharmChannel(cp, channel)
            logger.info('Checking resources released for  charm "%s" on track'
                        ' "%s" for optional bases "%s"',
                        cp.charmhub_name, channel, ','.join(args.bases or []))
            cp.repair_resource(channel=src_channel,
                               bases=args.bases or [],
                               dry_run=not args.confirmed,
                               retries=args.retries)


def validate_config_main(args: argparse.Namespace,
                         gc: GroupConfig,
                         ):
    """Validate configuration files based on the schema"""
    # This subcommand is a NOOP, if the execution reached to this point it
    # means the configuration was loaded successfully and validated correctly
    # by the schema, so there is no need do anything else than inform the user
    # the configuration is valid.
    print("The configuration is valid according to the schema.")


def setup_logging(loglevel: str) -> None:
    """Sets up some basic logging."""
    logging.basicConfig(format=LOGGING_FORMAT)
    logger.setLevel(getattr(logging, loglevel, 'ERROR'))
    cp_setup_logging(loglevel)
    lpt_setup_logging(loglevel)
    ch_setup_logging(loglevel)


def main(argv: Optional[List[str]] = None):
    """Main entry point.

    :param argv: The list of command line arguments to parse.
    """
    config_from_file = get_file_config()
    args = parse_args(argv, config_from_file)
    setup_logging(args.loglevel)

    config_dir = check_config_dir_exists(
        pathlib.Path(args.config_dir).expanduser().resolve())
    logger.info('Using config dir %s (full: %s)',
                args.config_dir, config_dir)

    # # Load the various project group configurations
    files = get_group_config_filenames(config_dir,
                                       args.project_groups)

    # To validate the configuration there is no need to login.
    if args.func == validate_config_main:
        args.anonymous = True

    lpt = LaunchpadTools(anonymous=args.anonymous)

    gc = GroupConfig(lpt)
    gc.load_files(files)
    if not list(gc.projects()):
        logger.error('No projects found; '
                     'are you sure the path is correct?: %s', config_dir)
        sys.exit(1)
    if not list(gc.projects(select=args.charms)):
        logger.error('No charms found; are you sure the arguments are correct')
        sys.exit(1)

    # Call the function associated with the sub-command.
    args.func(args, gc)


def cli_main():
    """CLI entry point for program."""
    try:
        main()
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except AssertionError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error: %s", str(e))
        raise


if __name__ == '__main__':
    cli_main()
