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

import collections
import json
import logging
import subprocess
import tempfile
from typing import (Any, Dict, List, Tuple, IO, Optional)
import sys
import time

import lazr.restfulclient.errors

from contextlib import suppress

import requests

from .launchpadtools import LaunchpadTools, TypeLPObject
from .charmhub import authorize_from_macaroon_dict


BUILD_SUCCESSFUL = 'Successfully built'
ERROR_PATTERNS = '(ERROR|ModuleNotFoundError)'
DEFAULT_RECIPE_FORMAT = '{project}.{branch}.{track}'

logger = logging.getLogger(__name__)


def setup_logging(loglevel: str) -> None:
    """Sets up some basic logging."""
    logger.setLevel(getattr(logging, loglevel, 'ERROR'))


class CharmProject:
    """Represents a CharmProject.

    The CharmProject is defined in a yaml file and has the following form:

    name: the human friendly name of the project
    charmhub: the charmhub store name
    launchpad: the launchpad project name
    team: the team who should own the branches and charm recipes
    repo: a URL to the upstream repository to be mirrored in
          launchpad
    branches: a list of branch -> recipe_info mappings for charm recipes on
            launchpad.

    The branch_info dictionary consists of the following keys:

      * channels (optional) - a list of fully qualified channel names to
          publish the charm to after building.
      * build-path (optional) - subdirectory within the branch containing
          metadata.yaml
      * recipe-name (optional) - A string used to format the name of the
          recipe. The project name will be passed as 'project', the branch
          name will be passed as 'branch', and the track name will be passed
          as 'track'. The default recipe-name is '{project}.{branch}.{track}'.
      * auto-build (optional) - a boolean indicating whether to automatically
          build the charm when the branch changes. Default value is True.
      * upload (optional) - a boolean indicating whether to upload to the store
          after a charm is built. Default value is True.
      * build-channels (optional) - a dictionary indicating which channels
          should be used by the launchpad builder for building charms. The
          key is the name of the snap or base and the value is the full
          channel identifier (e.g. latest/edge). Currently, Launchpad accepts
          the following keys: charmcraft, core, core18, core20 and core22.

    The following examples provide information for various scenarios.

    The following example uses all launchpad builder charm_recipe defaults
    publishes the main branch to the latest/edge channel and the stable
    branch to the latest/stable channel:

    name: Awesome Charm
    charmhub: awesome
    launchpad: charm-awesome
    team: awesome-charmers
    repo: https://github.com/canonical/charm-awesome-operator
    branches:
      main:
        channels: latest/edge
      stable:
        channels: latest/stable

    The following example builds a charm using the latest/edge channel of
    charmcraft, and does not upload the results to the store

    name: Awesome Charm
    charmhub: awesome
    launchpad: charm-awesome
    team: awesome-charmers
    repo: https://github.com/canonical/charm-awesome-operator
    branches:
      main:
        store-upload: False
        build-channels:
          charmcraft: latest/edge

    The following example builds a charm on the main branch of the git
    repository and publishes the results to the yoga/edge and latest/edge
    channels and builds a charm on the stable/xena branch of the git
    repository and publishes the results to xena/edge.

    name: Awesome Charm
    charmhub: awesome
    launchpad: charm-awesome
    team: awesome-charmers
    repo: https://github.com/canonical/charm-awesome-operator
    branches:
      main:
        channels:
          - yoga/edge
          - latest/edge
      stable/xena:
        channels:
          - xena/edge
    """

    def __init__(self, config: Dict[str, Any], lpt: 'LaunchpadTools'):
        self.lpt = lpt
        self.name: str = config.get('name')  # type: ignore
        self.team: str = config.get('team')  # type: ignore
        self._lp_team = None
        self.charmhub_name: str = config.get('charmhub')  # type: ignore
        self.launchpad_project: str = config.get('launchpad')  # type: ignore
        self._lp_project = None
        self.repository: str = config.get('repository')  # type: ignore
        self._lp_repo = None

        self.branches: Dict[str, Dict[str, Any]] = {}

        self._add_branches(config.get('branches', {}))

    def _add_branches(self, branches_spec: Dict[str, Dict]) -> None:
        default_branch_info = {
            'auto-build': True,
            'upload': True,
            'recipe-name': '{project}.{branch}.{track}'
        }
        for branch, branch_info in branches_spec.items():
            ref = f'refs/heads/{branch}'
            if ref not in self.branches:
                self.branches[ref] = dict(default_branch_info)
            if type(branch_info) != dict:
                raise ValueError('Expected a dict for key branches, '
                                 f' instead got {type(branch_info)}')

            self.branches[ref].update(branch_info)

    def merge(self, config: Dict[str, Any]) -> None:
        """Merge config, by overwriting."""
        self.name = config.get('name', self.name)
        self.team = config.get('team', self.team)
        self.charmhub_name = config.get('charmhub', self.charmhub_name)
        self.launchpad_project = config.get('launchpad',
                                            self.launchpad_project)
        self.repository = config.get('repository', self.repository)
        self._add_branches(config.get('branches', {}))

    @property
    def lp_team(self) -> TypeLPObject:
        """Return the launchpadlib object for the team.

        This is cached as it's used several times and is quite expensive to
        produce.
        """
        if self._lp_team:
            return self._lp_team
        self._lp_team = self.lpt.get_lp_team_for(self.team)
        return self._lp_team

    @property
    def lp_project(self) -> TypeLPObject:
        """Return the launchpadlib object for the project."""
        if self._lp_project:
            return self._lp_project
        self._lp_project = self.lpt.get_lp_project_for(self.launchpad_project)
        return self._lp_project

    @property
    def lp_repo(self) -> TypeLPObject:
        """Return the launchpadlib object for the repository, if configured."""
        if self._lp_repo:
            return self._lp_repo
        self._lp_repo = self.lpt.get_git_repository(
            self.lp_team, self.lp_project)
        return self._lp_repo

    def ensure_git_repository(self,
                              dry_run: bool = True
                              ) -> Optional[TypeLPObject]:
        """Ensure that launchpad project git repository exists.

        Configures launchpad project repositories for self (the charm)
        project. This function will validate that a git repository is
        configured in launchpad to import the git tree from the upstream
        project repository and that the git repository is set as the default
        code repository for the launchpad project.

        :param dry_run: if True, the default, then the function will just check
            if the git repository is being mirrored and bail if it isn't.
        :returns: the launchpad repository object
        """
        logger.info('Checking Launchpad git repositories for %s.',
                    self.name)

        if self.lp_project.owner != self.lp_team:
            logger.error('Project owner of project %s '
                         'does not match owner specified %s',
                         self.launchpad_project, self.team)
            raise ValueError(
                f'Unexpected project owner for {self.launchpad_project}')

        if self.lp_repo is None:
            logger.info('Git repository for project %s and '
                        '%s does not exist, importing now from %s',
                        self.lp_project.name, self.lp_team.name,
                        self.repository)
            if dry_run:
                print("Git repository doesn't exist, but dry_run is set, so "
                      "not setting up git repository mirroring and bailing "
                      "out.")
                return
            self._lp_repo = self.lpt.import_repository(
                self.lp_team, self.lp_project, self.repository)
            self.lp_repo.lp_refresh()
        else:
            logger.debug('Git repository for project %s and '
                         '%s already exists.',
                         self.lp_project.name, self.lp_team.name)

        # Check whether the repository is the default repository for the
        # project or not.
        if not self.lp_repo.target_default:
            logger.info('Setting default repository for %s to %s',
                        self.lp_project.name, self.lp_repo.git_https_url)
            if dry_run:
                print("Git target repostiroy isn't set, but dry_run, bailing "
                      "early.")
                return
            try:
                self.lpt.set_default_repository(self.lp_project, self.lp_repo)
                self.lp_repo.lp_refresh()
            except Exception:  # no-qa
                # Log the error, but don't fail if we couldn't set the
                # default repository. Typically means the team is not the
                # owner of the project.
                logger.error(
                    'Failed to set the default repository for %s to %s',
                    self.lp_project.name, self.lp_repo.git_https_url)

        if not self.lp_project.vcs:
            logger.info('Setting project %s vcs to Git', self.lp_project.name)
            if dry_run:
                print("LP project is not set, but dry_run, bailing early.")
                return
            self._lp_project = None  # force a refetch of the project
            self.lp_project.vcs = 'Git'
            attempts = 0
            while True:
                try:
                    self.lp_project.lp_save()
                    break
                except lazr.restfulclient.errors.PreconditionFailed:
                    if attempts > 5:
                        logger.error("Repeated Precondition failure!")
                        raise
                    logger.info(
                        'Got precondition error; refetching project and '
                        'trying again.')
                    time.sleep(5.0)
                    self._lp_project = None  # force a refetch of the project
                    attempts += 1

        return self.lp_repo

    @staticmethod
    def _get_git_repository(lpt: 'LaunchpadTools',
                            lp_team: TypeLPObject,
                            lp_project: TypeLPObject,
                            ) -> TypeLPObject:
        """Ensure charm recipes in Launchpad matches CharmProject's conf.

        :param lpt: the launchpad tools object to do things in launchpad.
        :param lp_team: the lp team object
        :param lp_project: the lp project object
        :returns: the lp repoistory object
        :raises ValueError: if the repository can't be found.
        """
        lp_repo = lpt.get_git_repository(lp_team, lp_project)
        if not lp_repo:
            raise ValueError(
                f'Unable to find repository for team {lp_team.name} '
                f'and project {lp_project.name}')
        return lp_repo

    def ensure_charm_recipes(self,
                             branches: Optional[List[str]] = None,
                             remove_unknown: bool = False,
                             dry_run: bool = True,
                             ) -> None:
        """Ensure charm recipes in Launchpad matches CharmProject's conf.

        :param branches: If supplied, then filter the recipes based on the
            branches supplied.
        :param remove_unknown: If True then unknown recipes will be removed.
        :param dry_run: If True then actions are not actually undertaken, but
            are printed to the console instead.
        """
        print(f'Checking charm recipes for charm {self.name}')
        logger.debug(str(self))
        try:
            self.lp_project
        except KeyError:
            logger.error(
                "Can't continue; no project in Launchpad called '%s'",
                self.launchpad_project)
        try:
            self.lp_repo
        except ValueError:
            logger.error(
                "Can't continue; no repository defined for %s",
                self.launchpad_project)
            return

        current = self._calc_recipes_for_repo(filter_by=branches)
        if current['missing_branches_in_repo']:
            # This means that there are required channels, but no branches in
            # the repo; need to log this fact.
            print(
                "The following branches are missing from the repository "
                "but are configured as branches for recipes.")
            for branch in current['missing_branches_in_repo']:
                print(f" - {branch}")
        any_changes = (any(not(r['exists']) or r['changed']
                           for r in current['in_config_recipes'].values()))
        if not(any_changes) and not(current['non_config_recipes']):
            print("No changes needed.")
            return

        # Create recipes that are missing and/o update recipes that have
        # changes.
        logger.debug('in_config_recipes={}'.format(
            current['in_config_recipes']))
        for recipe_name, state in current['in_config_recipes'].items():
            if state['exists'] and state['changed']:
                # it's an update
                lp_recipe = state['current_recipe']
                print(f'Charm recipe {lp_recipe.name} has changes. Saving.')
                print("Changes: {}".format(", ".join(state['changes'])))
                if dry_run:
                    print("Would update but dry_run")
                else:
                    for rpart, battr in state['updated_parts'].items():
                        setattr(lp_recipe, rpart, battr)
                    lp_recipe.lp_save()
            elif not(state['exists']):
                if dry_run:
                    print(f'Would create recipe {recipe_name} (dry_run)')
                else:
                    print(f'Creating charm recipe for {recipe_name}')
                    build_from = state['build_from']
                    lp_recipe = self.lpt.create_charm_recipe(
                        recipe_name=recipe_name,
                        branch_info=build_from['branch_info'],
                        lp_branch=build_from['lp_branch'],
                        owner=self.lp_team,
                        project=self.lp_project,
                        store_name=self.charmhub_name,
                        channels=build_from['channels'])
                    print(f'Created charm recipe {lp_recipe.name}')

            else:
                print(f'No changes needed for charm recipe {recipe_name}')

        # If remove_unknown option is used, then delete the unknown recipes.
        if remove_unknown and current['non_config_recipes']:
            for recipe_name in current['non_config_recipes'].keys():
                if dry_run:
                    print(
                        f'Would delete {self.lp_project.name} - {recipe_name}'
                        f' (dry_run)')
                else:
                    self.lpt.delete_charm_recipe_by_name(
                        recipe_name,
                        self.lp_team,
                        self.lp_project)

    def delete_recipe_by_name(self,
                              recipe_name: str,
                              dry_run: bool = True,
                              ) -> None:
        """Delete a recipe filtered by it's full name.

        :param recipe_name: the recipe name
        :raises KeyError: if the recipe couldn't be found.
        """
        if dry_run:
            print(f'Would delete {self.lp_project.name} - {recipe_name} '
                  f'(dry_run)')
        else:
            self.lpt.delete_charm_recipe_by_name(
                recipe_name,
                self.lp_team,
                self.lp_project)

    def delete_recipe_by_branch_and_track(self,
                                          branch: str,
                                          track: str,
                                          dry_run: bool = True,
                                          ) -> None:
        """Delete a recipe filtered by track and risk.

        If the recipe doesn't exist a warning is printed.

        :param branch: the branch to delete
        :param track: the track to delete.
        :raises KeyError: if the recipe couldn't be found.
        """
        branch_name = branch.replace('/', '-')
        recipe_name = DEFAULT_RECIPE_FORMAT.format(
            project=self.lp_project.name,
            branch=branch_name,
            track=track)
        if dry_run:
            print(f'Would delete {recipe_name} (dry_run)')
        else:
            self.lpt.delete_charm_recipe_by_name(
                recipe_name,
                self.lp_team,
                self.lp_project)

    def _calc_recipes_for_repo(self,
                               filter_by: Optional[List[str]] = None,
                               ) -> Dict:
        """Calculate the set of recipes for a repo based on the config.

        Return a calculated set of repo branches, channels, recipe names and
        their configuration.

        The repo_branches is an OrderedDict of repo branch -> List[recipe_name]
        The channels ...

        :param filter_by: filter the recipes based on the branches passed.
        :returns: A dictionary of recipes for the repo filtered by branches if
            supplied.
        """
        lp_recipes = self.lpt.get_charm_recipes(self.lp_team, self.lp_project)
        charm_lp_recipe_map = {recipe.name: recipe for recipe in lp_recipes}

        # a recipe_name: {info for recipe}  dictionary
        all_recipes: Dict[str, Dict] = collections.OrderedDict()
        no_recipe_branches: List[str] = []
        mentioned_branches: List[str] = []

        if self.lp_repo:
            for lp_branch in self.lp_repo.branches:
                mentioned_branches.append(lp_branch.path)
                branch_info = self.branches.get(lp_branch.path, None)
                if not branch_info:
                    logger.info(
                        'No tracks configured for branch %s, continuing.',
                        lp_branch.path)
                    no_recipe_branches.append(lp_branch.path)
                    continue

                # Variable to cache whether filtering is happening
                are_filtering = False
                # filter_by is a list of branches, but lp_branch.path
                # includes the "refs/heads/" part, so we actually need a
                # more complex filter below
                if filter_by:
                    _branch = lp_branch.path
                    if _branch.startswith("refs/heads/"):
                        _branch = _branch[len("refs/heads/"):]
                    if _branch not in filter_by:
                        are_filtering = True

                # Strip off refs/head/. And no / allowed, so we'll replace
                # with _
                branch_name = (lp_branch.path[len('refs/heads/'):]
                               .replace('/', '-'))
                recipe_format = branch_info['recipe-name']
                upload = branch_info.get('upload', True)
                # Get the channels; we have to do a separate recipe for each
                # channel that doesn't share the same track.  Reminder:
                # channels are <track>/<risk>
                channels = branch_info.get('channels', None)
                if upload and channels:
                    tracks = self._group_channels(channels)
                else:
                    tracks = (("latest", []),)
                for track, track_channels in tracks:
                    recipe_name = recipe_format.format(
                        project=self.lp_project.name,
                        branch=branch_name,
                        track=track)

                    # Popping recipes needs to happen before filtering so that
                    # they are not 'unknown' recipes and don't get deleted.
                    lp_recipe = charm_lp_recipe_map.pop(recipe_name, None)

                    # Now if fitlering just continue
                    if are_filtering:
                        continue

                    if lp_recipe:
                        # calculate diff
                        changed, updated_dict, changes = (
                            self.lpt.diff_charm_recipe(
                                recipe=lp_recipe,
                                # auto_build=branch_info.get('auto-build'),
                                auto_build=branch_info['auto-build'],
                                auto_build_channels=branch_info.get(
                                    'build-channels', None),
                                build_path=branch_info.get('build-path', None),
                                store_channels=track_channels,
                                store_upload=branch_info['upload']))

                        all_recipes[recipe_name] = {
                            'exists': True,
                            'changed': changed,
                            'current_recipe': lp_recipe,
                            'updated_parts': updated_dict,
                            'changes': changes,
                        }
                    else:
                        all_recipes[recipe_name] = {
                            'exists': False,
                            'changed': False,
                            'current_recipe': None,
                            'updated_recipe': None,
                            'changes': [],
                        }
                    all_recipes[recipe_name].update({
                        'build_from': {
                            'recipe_name': recipe_name,
                            'branch_info': branch_info,
                            'lp_branch': lp_branch,
                            'lp_team': self.lp_team,
                            'lp_project': self.lp_project,
                            'store_name': self.charmhub_name,
                            'channels': track_channels
                        }
                    })
        return {
            'lp_recipes': lp_recipes,
            'non_config_recipes': charm_lp_recipe_map,
            'in_config_recipes': all_recipes,
            'no_recipe_branches': no_recipe_branches,
            'missing_branches_in_repo': list(
                sorted(set(self.branches.keys() - set(mentioned_branches)))),
        }

    def print_diff(self,
                   detail: bool = False,
                   file: IO = sys.stdout) -> None:
        """Print a diff between desired config and actual config.

        :param detail: print detailed output if True
        :param file: where to send the output.
        """
        logger.info(f'Printing diff for: {self.name}')
        try:
            self.lp_project
        except KeyError:
            print(f"{self.name[:35]:35} -- Project doesn't exist!!: "
                  f"{self.launchpad_project}", file=file)
            return
        try:
            self.lp_repo
        except ValueError:
            print(f"{self.name[:35]:35} -- No repo configured!", file=file)
            return
        info = self._calc_recipes_for_repo()
        any_changes = (any(not(r['exists']) or r['changed']
                           for r in info['in_config_recipes'].values()))
        change_text = ("Changes required"
                       if any_changes or info['missing_branches_in_repo']
                       else "No changes needed")
        extra_recipes_text = (
            f" - {len(info['non_config_recipes'].keys())} extra config recipes"
            if info['non_config_recipes'] else "")
        print(
            f"{self.name[:35]:35} {change_text:20}{extra_recipes_text}",
            file=file)
        if detail:
            # Print detail from info.
            if info['non_config_recipes']:
                print(" * Recipes that have no corresponding config:",
                      file=file)
                for recipe_name in info['non_config_recipes'].keys():
                    print(f"   - {recipe_name}", file=file)
            if any_changes:
                print(" * recipes that require changes:", file=file)
                for recipe_name, detail_ in info['in_config_recipes'].items():
                    if not(detail_['exists']):
                        print(f"    - {recipe_name:35} : Needs creating.",
                              file=file)
                    elif detail_['changed']:
                        print(f"    - {recipe_name:35} : "
                              f"{','.join(detail_['changes'])}", file=file)
            if info['missing_branches_in_repo']:
                print(" * missing branches in config but not in repo:",
                      file=file)
                for branch in info['missing_branches_in_repo']:
                    print(f'    - {branch[len("refs/heads/"):]}', file=file)
        # pprint.pprint(info)

    def show_lauchpad_config(self,
                             file: IO = sys.stdout
                             ) -> None:
        """Print out the launchpad config for the charms, if any.
        """
        logger.info(f'Printing launchpad info for: {self.name}')
        try:
            self.lp_project
        except KeyError:
            print(f"{self.name[:35]:35} -- Project doesn't exist!!: "
                  f"{self.launchpad_project}", file=file)
            return
        print(f"{self.name}:", file=file)
        print(f" * launchpad project: {self.launchpad_project}", file=file)
        try:
            self.lp_repo
        except ValueError:
            print(f"{self.name[:35]:35} -- No repo configured!", file=file)
            return
        print(f" * repo: {self.repository}")
        info = self._calc_recipes_for_repo()
        if info['in_config_recipes']:
            print(" * Recipes configured in launchpad matching channels:",
                  file=file)
            for name, detail in info['in_config_recipes'].items():
                if detail['current_recipe']:
                    branch = (
                        detail['current_recipe']
                        .git_ref.path[len('refs/heads/'):])
                    channels = ', '.join(detail['current_recipe']
                                         .store_channels)
                    print(f"   - {name[:40]:40} - "
                          f"git branch: {branch[:20]:20} "
                          f"channels: {channels}",
                          file=file)

    def get_builds(self,
                   channel: str = None,
                   arch_tag: str = None,
                   detect_error: bool = False
                   ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Get the builds associated to a charm.

        The builds are organized in a dictionary where the key is the recipe's
        name that holds another dictionary that uses the <series>/<arch>
        string as the key and its value is a dictionary with the relevant
        attributes.

        Example:

        {'charm-nova-compute.master.latest': {
            'focal/amd64': {
                'datebuild': <datetime.datetime instance>,
                'store_channels': ['latest/edge'],
                'buildstate': 'Successfully built',
                'build_log_url': 'https://launchpadlibrarian.net/58295/...',
                'error_detected': <list of errors found in the log>
                }
            }
        }

        :param channel: filter list of builds by channel (e.g. 'latest/edge')
        :param arch_tag: filter list of build by architecture (e.g. 'amd64')
        :param detect_error: Attempt to found errors in the building log when
                             the built was not successful.
        """
        lp_recipes = self.lpt.get_charm_recipes(self.lp_team, self.lp_project)
        builds = collections.defaultdict(dict)
        for recipe in lp_recipes:

            if channel and channel not in recipe.store_channels:
                logger.debug((f'Skipping recipe {recipe.name}, because '
                              f'"{channel}" not in {recipe.store_channels}'))
                continue

            logger.debug(f'Getting builds for recipe {recipe.name}')
            for build in recipe.builds:
                build_arch_tag = build.distro_arch_series.architecture_tag
                if arch_tag and arch_tag != build_arch_tag:
                    logger.debug((f'Skipping build of arch {build_arch_tag} '
                                  f'of recipe {recipe.name}'))
                    continue

                series_arch = f'{build.distro_series.name}/{build_arch_tag}'
                logger.info((f'Found build of {recipe.name} for {series_arch} '
                             f'in {recipe.store_channels}'))
                date = build.datebuilt
                if (series_arch not in builds[recipe.name] or
                        (date and
                         builds[recipe.name][series_arch]['datebuilt'] < date
                         )):
                    error_detected = None
                    if detect_error and build.buildstate != BUILD_SUCCESSFUL:
                        log_url = build.build_log_url
                        try:
                            error_detected = self._detect_error(log_url)
                        except Exception:
                            logger.warn(f'Not able to detect error: {log_url}')

                    logger.debug((f'Adding {recipe.name}/{series_arch} to the '
                                  f'list of builds'))
                    builds[recipe.name][series_arch] = {
                        'datebuilt': build.datebuilt,
                        'store_channels': recipe.store_channels,
                        'buildstate': build.buildstate,
                        'build_log_url': build.build_log_url,
                        'error_detected': error_detected,
                        'revision': build.revision_id,
                        'store_upload_revision': build.store_upload_revision,
                        'store_upload_status': build.store_upload_status,
                        'store_upload_error_message':
                            build.store_upload_error_message,
                    }
        return builds

    @staticmethod
    def _detect_error(url: str) -> List[str]:
        build_log = requests.get(url)

        errors_found = []

        with tempfile.NamedTemporaryFile() as f:
            f.write(build_log.content)
            f.flush()

            with suppress(subprocess.CalledProcessError):
                errors_found.append(
                    subprocess.check_output(['zgrep', '-P', ERROR_PATTERNS,
                                             f.name],
                                            universal_newlines=True)
                )

        return errors_found

    def authorize(self, branches: List[str], force: bool = False) -> None:
        """Authorize a charm's recipes, filtered by branches.

        Authorize a charm's recipes.  The list of recipes to authorize is
        filtered by the branch provided.  If the branch doesn't exist, then a
        warning is logged, but no error is raised.

        NOTE: currently, the authorization is done via web-browser.

        :param branches: a list of branches to match to find the recipes.
        :param force: if True, do authorization even if LP thinks it is already
            authorized.
        """
        print(f"Authorizing recipes for {self.charmhub_name} ({self.name})")
        if branches:
            print(" .. for branch{}: {}".format(
                ('' if len(branches) == 1 else 'es'),
                ', '.join(branches)))
        info = self._calc_recipes_for_repo()
        for recipe_name, in_config_recipe in info['in_config_recipes'].items():
            branch_path = (
                in_config_recipe['build_from']['lp_branch'].path or '')
            if branch_path.startswith('refs/heads/'):
                branch_path = branch_path[len('refs/heads/'):]
            if branches and (branch_path not in branches):
                logger.info("Ignoring branch: %s as not in branches match.",
                            branch_path)
                continue
            print(f'Branch is: {branch_path}')
            current_recipe = in_config_recipe['current_recipe']
            if current_recipe is not None:
                if not(current_recipe.can_upload_to_store) or force:
                    print(f"Doing authorization for recipe: {recipe_name} on "
                          f"branch: {branch_path} for charm: "
                          f"{self.charmhub_name}")
                    self._do_authorization(current_recipe)
                else:
                    print(f"Recipe: {recipe_name} is already authorized.")
            else:
                print(f"Recipe: {recipe_name} does not exist in Launchpad "
                      f"for charm: {self.charmhub_name}")

    def _do_authorization(self, recipe: TypeLPObject) -> None:
        """Do the authorization for a recipe.

        :param recipe: a LP object that is for the recipe to auth.
        """
        try:
            macaroon_dict = json.loads(recipe.beginAuthorization())
            result = authorize_from_macaroon_dict(macaroon_dict)
            recipe.completeAuthorization(discharge_macaroon=result)
        # blanket catch.  This is part of serveral attempts, so we don't want
        # to stop trying just because one fails.  If all fail, it'll be pretty
        # obvious!
        except Exception as e:
            logger.error(
                "Failed authenticating for upload.  Recipe: %s "
                "Reason: %s", recipe.name, str(e))

    @staticmethod
    def _group_channels(channels: List[str],
                        ) -> List[Tuple[str, List[str]]]:
        """Group channels into compatible lists.

        The charmhub appears to only allow a recipe to target a single channel,
        but with multiple levels of risk and/or 'branches'.  The specs for
        channels are either 'latest' or 'latest/<risk>'.  In this case, the
        grouping would be
        [('latest', ['latest', 'latest/edge', 'latest/stable']),]

        :param channels: a list of channels to target in the charmhub
        :returns: the channels, grouped by track.
        """
        groups = collections.OrderedDict()
        for channel in channels:
            if '/' in channel:
                group, _ = channel.split('/', 1)
            else:
                group = channel
            try:
                groups[group].append(channel)
            except KeyError:
                groups[group] = [channel]
        return list(groups.items())

    def __repr__(self):
        return (f"CharmProject(name={self.name}, team={self.team}, "
                f"charmhub_name={self.charmhub_name}, "
                f"launchpad_project={self.launchpad_project},"
                f"repository={self.repository}, "
                f"branches={self.branches})")

    def __str__(self):
        branches = []
        width = 20
        for branch, spec in self.branches.items():
            if branch.startswith("refs/heads/"):
                bname = branch[len("refs/heads/"):]
            else:
                bname = branch
            channels = ", ".join(spec['channels'])
            branches.append(f"{bname} -> {channels}")
        branches_str = ''
        if branches:
            branches_str = f"{'branches':>{width}}: {branches[0]}"
            for br in branches[1:]:
                branches_str += f"\n{':':>{width+1}} {br}"

        return (f"CharmProject:\n"
                f"{'name':>{width}}: {self.name}\n"
                f"{'team':>{width}}: {self.team}\n"
                f"{'charmhub_name':>{width}}: {self.charmhub_name}\n"
                f"{'launchpad_project':>{width}}: {self.launchpad_project}\n"
                f"{'repository':>{width}}: {self.repository}\n"
                f"{branches_str}")
