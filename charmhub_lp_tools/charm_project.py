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
from typing import (Any, Dict, Generator, List, Tuple, IO, Optional, Set)
import sys
import time
import yaml

from contextlib import suppress

import lazr.restfulclient.errors
import requests
import requests_cache

from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from .launchpadtools import LaunchpadTools, TypeLPObject
from .charmhub import authorize_from_macaroon_dict, get_store_client
from .exceptions import CharmcraftError504

# build states
BUILD_SUCCESSFUL = 'Successfully built'
CURRENTLY_BUILDING = 'Currently building'
UPLOADING_BUILD = 'Uploading build'
NEEDS_BUILDING = 'Needs building'
FAILED_TO_BUILD = 'Failed to build'
FAILED_TO_UPLOAD = 'Failed to upload'

ERROR_PATTERNS = '(ERROR|ModuleNotFoundError)'
DEFAULT_RECIPE_FORMAT = '{project}.{branch}.{track}'

CHARMHUB_BASE = "https://api.charmhub.io/v2/charms"
CHARMCRAFT_ERROR_504 = ('Issue encountered while processing your request: '
                        '[504] Gateway Time-out.')

logger = logging.getLogger(__name__)
requests_session = requests_cache.CachedSession(':memory:')


def setup_logging(loglevel: str) -> None:
    """Sets up some basic logging."""
    logger.setLevel(getattr(logging, loglevel, 'ERROR'))


def run_charmcraft(
        cmd: List[str],
        check: bool,
        retries: int = 0,
) -> Optional[subprocess.CompletedProcess]:
    """Run charmcraft.

    :param cmd: charmcraft command to run, passed as it is to subprocess.run().
    :param check: If check is True and the exit code was non-zero, it raises
                  a CalledProcessError.
    :param retries: Retry if charmhub responds with a 500 error.
    :raises: subprocess error if the charmcraft command fails.
    """
    for attempt in Retrying(wait=wait_fixed(1),
                            retry=retry_if_exception_type(CharmcraftError504),
                            reraise=True,
                            stop=stop_after_attempt(retries)):
        with attempt:
            try:
                p = subprocess.run(cmd,
                                   check=check,
                                   text=True,
                                   # combine stdout and stderr
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
                return p
            except subprocess.CalledProcessError as ex:
                logger.error(ex.stdout)
                if ex.stdout and CHARMCRAFT_ERROR_504 in ex.stdout:
                    raise CharmcraftError504()
                else:
                    raise


class CharmChannel:

    def __init__(self, project: 'CharmProject', name: str):
        if '/' in name:
            self.name = name
            (self.track, self.risk) = self.name.split('/')
        else:
            self.name = f"{name}/stable"
            self.track = name
            self.risk = "stable"
        self.project = project
        self.log = logging.getLogger(f'{__name__}.{self.__class__.__name__}')

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'CharmChannel<{self.name}>'

    def __eq__(self, other: 'CharmChannel'):
        return (self.project.charmhub_name, self.name) == \
            (other.project.charmhub_name, other.name)

    def __hash__(self):
        return hash((self.project.charmhub_name, self.name))

    @property
    def channel_map(self):
        return self.project.charmhub_channel_map

    def close(
            self,
            dry_run: bool = True,
            check: bool = True,
            retries: int = 0,
    ) -> Optional[subprocess.CompletedProcess]:
        """Close the channel.

        :param dry_run: if True run 'charmcraft close', otherwise just log
                        the command.
        :param check: If check is True and the exit code was non-zero, it
                      raises a CalledProcessError.
        :param retries: Retry if charmhub responds with a 500 error.
        :returns: an instance of CompletedProcess if dry_run is False,
                  otherwise None
        """
        cmd = ['charmcraft', 'close', self.project.charmhub_name, self.name]
        if dry_run:
            print(' '.join(cmd), ' # dry-run mode')
        else:
            self.log.debug('Running: %s', ' '.join(cmd))
            return run_charmcraft(cmd, check=check, retries=retries)

    @staticmethod
    def get_resources_from_metadata(metadata: Dict) -> List[str]:
        """Retrieve the metadata as a dictionary.

        If no resources are specified in the metadata then return an empty
        list.

        :returns: the list of resource names.
        """
        return [k for k in metadata.get('resources', {}).keys()]

    def get_charm_metadata_for_channel(self) -> Optional[Dict]:
        """Get resource names from charm metadata.

        If the track/risk actually matches a being asked for actually matches a
        repository branch, then attempt to extract the metadata.yaml file,
        parse it into YAML and then return this.  If it doesn't match a branch
        or the file can't be loaded, then it is ignored, and a None return.

        :returns: the dictionary loaded from the metadata.yaml if it exists,
            otherwise None.
        """
        repo = self.project.repository
        # first see if we find the branch from the track/risk
        # if we can't then no point in continuing
        find_channel = f"{self.track}/{self.risk}"
        found_branch = None
        for branch, branch_data in self.project.branches.items():
            for channel in branch_data['channels']:
                if channel == find_channel:
                    # found the branch, let's set it.
                    if branch.startswith("refs/heads/"):
                        found_branch = branch[len("refs/heads/"):]
                    else:
                        found_branch = branch
                    # break the inner loop
                    break
            else:
                # continue outer loop if the inner loop wasn't broken
                continue
            # Inner loop was broken, break outer loop
            break
        if found_branch is None:
            self.log.debug("get_charm_metadata_for_channel: "
                           "couldn't find branch, so returning None.")
            return None
        if repo.startswith("https://opendev.org/"):
            # repo is https://opendev.org/openstack/charm-keystone.git
            # url is https://opendev.org/{user}/{charm}/raw/branch/
            #              {branch}/metadata.yaml
            user_charm = repo[len("https://opendev.org/"):]
            (user, charm) = user_charm.split('/')
            if charm.endswith(".git"):
                charm = charm[:-4]
            url = (f"https://opendev.org/{user}/{charm}/raw/branch/"
                   f"{found_branch}/metadata.yaml")
            # use requests to get the metadata
            try:
                self.log.debug("Getting metadata from '%s'", url)
                result = requests.get(url)
                if result.status_code != 200:
                    self.log.info("Error getting metadata, code=%s",
                                  result.status_code)
                    return None
                result_text = result.text.strip()
                if result_text == "src/metadata.yaml":
                    url = (f"https://opendev.org/{user}/{charm}/raw/branch/"
                           f"{found_branch}/src/metadata.yaml")
                    self.log.debug("Following link: getting metadata from"
                                   " '%s'", url)
                    result = requests.get(url)
                    result_text = result.text.strip()
                try:
                    return yaml.safe_load(result_text)
                except Exception as e:
                    self.log.error(
                        "Couldn't decode response due to %s\nResponse:\n%s",
                        str(e), result.text)
            except Exception as e:
                self.log.error('Failed to fetch metadata.yam: %s', str(e))
                return None

        else:
            # don't know what to do with that one.
            self.log.error("Don't know how to fetch metadata.yaml from "
                           " %s", repo)
            return None

    def release(
            self,
            revision: int,
            dry_run: bool = True,
            check: bool = True,
            retries: int = 0,
            resource_names: Optional[List[str]] = None,
    ) -> Optional[subprocess.CompletedProcess]:
        """Release a charm's revision in the channel.

        Try to release a charm's revision into the channel. The caller can
        provide the resource_names that should be released alongside the charm,
        and if missing they will be found for the charm by searching for the
        highest revision available. The copy command will preferentially use
        the resources already assigned from the existing revision.  i.e. the
        additional 'search' for resources is to fix existing malformed charmhub
        releases that weren't released with revisions.  This is as best a fix
        up, but if not done, the release will fail.

        :param revision: charm's revision id to release
        :param dry_run: if True run 'charmcraft release', otherwise just log
                        the command.
        :param check: If check is True and the exit code was non-zero, it
                      raises a CalledProcessError.
        :param retries: Retry if charmhub responds with a 500 error.
        :param resource_names: optional list of resource names to check are
            going to be included when writing the revision to the target
            channel.
        :returns: an instance of CompletedProcess if dry_run is False,
                  otherwise None
        """
        cmd = ['charmcraft', 'release', self.project.charmhub_name,
               f'--revision={revision}', f'--channel={self.name}']

        (found_resources, missing_resources) = (
            self._resolve_resources_for_revision(revision, resource_names))
        for resource in found_resources + missing_resources:
            cmd.append(f'--resource={resource[0]}:{resource[1]}')
        if dry_run:
            print(' '.join(cmd), " # dry-run mode")
        else:
            print(f"Running: {' '.join(cmd)}")
            return run_charmcraft(cmd, check=check, retries=retries)

    def _resolve_resources_for_revision(
            self,
            revision: int,
            resource_names: Optional[List[str]] = None,
    ) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
        """Resolve the resources for a revision.

        Work out what resources should be on the revision.  If resource_names
        is supplied then that is used as the definitive resource names that
        should be on the revision.

        These are filled out with the resource revision numbers found against
        the revision.  They are then back-filled by looking at the resources
        themselves, and picking the highest number available.  This is unlikely
        to be correct, and is only used to 'fix' revisions which were either
        imported into charmhub from the charmstore, or were released prior to
        the charmhub enforcing revisions.

        :param revision: the integer revision number in the charmhub for this
            charm.
        :param resource_names: optional list of definitive resource names that
            should be on the revision.
        :returns: a tuple of (list of (resource name, resource revision),
                             (list of (resource name, resource revision))
            where the lists are (found resources, missing resources)
        """
        resources = self.find_resources(revision)
        resource_names = resource_names or []
        # validate that all the resources exist
        names = [resource.name for resource in resources]
        insert_resources = []
        if list(sorted(names)) != list(sorted(resource_names)):
            missing_resources = list(set(resource_names) - set(names))
            self.log.warning("resources missing!: %s",
                             ','.join(missing_resources))
            for resource in missing_resources:
                revisions = self.get_resource_revisions(resource)
                if revisions:
                    # add the highest numbered revision available
                    insert_resources.append(
                        (resource, list(sorted(revisions))[-1]))
                else:
                    raise Exception(
                        f"Can't construct resource revision for {resource}")
        return ([(r.name, r.revision) for r in resources], insert_resources)

    def get_all_revisions(self) -> Set[int]:
        """Get all of the revisions in this channel.

        :returns: set of all revisions in the channel.
        """
        all_revisions = set()
        for channel_def in self.channel_map:
            revision = channel_def['revision']
            revision_num = revision['revision']
            chan_track = channel_def['channel']['track']
            chan_risk = channel_def['channel']['risk']
            if (chan_track, chan_risk) == (self.track, self.risk):
                all_revisions.add(revision_num)
        return all_revisions

    def get_revisions_for_bases(self,
                                bases: List[str],
                                arch: Optional[str] = None,
                                ignore_arches: Optional[List[str]] = None,
                                ) -> Set[int]:
        """Decode the channel and return a set of (revision, [arches]).

        :param base: base channel.
        :param arch: Filter by architecture
        :param ignore_arches: Filter by ignoring the following list of arches.
        :returns: The revision id associated with this channel.
        """
        if ignore_arches is None:
            _ignore_arches = set()
        else:
            _ignore_arches = set(ignore_arches)
        revisions_: Dict[str, Set[int]] = collections.defaultdict(set)
        for i, channel_def in enumerate(self.channel_map):
            base_arch = channel_def['channel']['base']['architecture']
            base_chan = channel_def['channel']['base']['channel']
            chan_track = channel_def['channel']['track']
            chan_risk = channel_def['channel']['risk']
            revision = channel_def['revision']
            revision_num = revision['revision']
            arches = [f"{v['architecture']}/{v['channel']}"
                      for v in revision['bases']]

            if set(v['architecture'] for v in
                   revision['bases']).intersection(_ignore_arches):
                # ignore any revisions that include any of the ignored
                # architecctures.
                continue

            if (
                    base_chan in bases and
                    (chan_track, chan_risk) == (self.track, self.risk) and
                    (arch is None or arch in arches)
            ):
                logger.debug(("%s (%s) -> base_arch=%s base_chan=%s "
                              "revision=%d channel=%s/%s -> arches=[%s]"),
                             self.project.charmhub_name, i, base_arch,
                             base_chan, revision_num, chan_track,
                             chan_risk, ", ".join(arches))
                for a in arches:
                    for base in bases:
                        if a[-len(base):] == base:
                            revisions_[a].add(revision_num)

        # add "all/<base>" arch revisions to the other revisions of the same
        # base.
        for base in bases:
            all_arch = f"all/{base}"
            if all_arch in revisions_:
                all_arch_revisions = revisions_[all_arch]
                delete = False
                for k in revisions_.keys():
                    if k != all_arch and k[-len(base):] == base:
                        revisions_[k].update(all_arch_revisions)
                        delete = True
                if delete:
                    del revisions_[all_arch]
        # now just keep the highest revision for each arch.
        highest_revisions = collections.defaultdict(int)
        for k, v in revisions_.items():
            highest_revisions[k] = list(sorted(revisions_[k]))[-1]
        # and collect the final set of revisions to keep.
        revisions = set(v for v in highest_revisions.values())
        return revisions

    def find_resources(
            self,
            revision: int
    ) -> List[object]:
        """Find resources associated to a revision.

        :param revision: revision number
        :returns: a list of resources that were released with a charm revision
        """
        store = get_store_client()
        channel_map = store.list_releases(self.project.charmhub_name)[0]

        for release in channel_map:
            if release.revision == revision:
                return release.resources

        return []  # no resources found

    def get_resource_revisions(self, resource_name: str) -> List[int]:
        """Get the revisions of a resource associated with a charm.

        :param resource_name: the resource to get a list of revisions for.
        :returns: the list of revisions (ints) for the named resource.
        """
        store = get_store_client()
        revisions = store.list_resource_revisions(
            self.project.charmhub_name,
            resource_name)
        return [r.revision for r in revisions]


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
      * bases (optional) - a list of bases (e.g. "18.04", "20.04") that should
          be present in the channels.  Note that this can be different to the
          charmcraft.yaml 'run-on' bases as it may allow for custom bases.
      * duplicate-channels (optional) - a list of bases which are duplicates of
          this channel. i.e. if the charm is to be provided to both the train
          and rocky channels, the this would be added to the train channel,
          with rocky as the duplicate assuming the the train branch/channel is
          the source for the charm.

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


    The follow example builds a charm on the main branch of the git repository
    and publishes the resutls to the yoga/edge and latest/edge channels, builds
    the charm on the stable/xena branch and publishes the results to the
    xena/edge channel.  Additionally, the xena/edge channel is restricted to
    the 20.04 base, and any operations on the xena channel are duplicated to
    the victoria channel (e.g. copy, clean)

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
        bases:
          - "20.04"
        duplicate-channels:
          - victoria
    """

    INFO_URL = CHARMHUB_BASE + "/info/{charm}?fields=channel-map"

    def __init__(self, config: Dict[str, Any], lpt: 'LaunchpadTools'):
        self.lpt = lpt
        self.name: str = config.get('name')  # type: ignore
        self.team: str = config.get('team')  # type: ignore
        self.log = logging.getLogger(f'{__name__}.{self.__class__.__name__}')
        self._lp_team = None
        self.charmhub_name: str = config.get('charmhub')  # type: ignore
        self.launchpad_project: str = config.get('launchpad')  # type: ignore
        self._lp_project = None
        self.repository: str = config.get('repository')  # type: ignore
        self.project_group: str = config.get('project_group')  # type: ignore
        self._lp_repo = None
        self._channels = None  # type: Optional[Set]
        self._raw_charm_info = None
        self._charmhub_tracks = None  # type: Optional[List[str]]

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
                raise ValueError(f'{self.charmhub_name}\n'
                                 f'Expected a dict for key branches, '
                                 f' instead got {type(branch_info)} - '
                                 f' {branch_info}')

            self.branches[ref].update(branch_info)

        # clear cached channels
        self._channels = None

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
    def raw_charm_info(self):
        if not self._raw_charm_info:
            self._raw_charm_info = requests.get(
                self.INFO_URL.format(charm=self.charmhub_name)
            )
        return self._raw_charm_info

    @property
    def charmhub_channel_map(self):
        try:
            self.raw_charm_info.encoding = 'utf-8'
            m = json.loads(self.raw_charm_info.text.strip())
            return m['channel-map']
        except json.JSONDecodeError as e:
            # it went wrong, let's print what we got:
            self.log.error("channel_map: It went horribly wrong: %s", str(e))
            self.log.error("Received:\n%s", self.raw_charm_info.text)
            raise

    @property
    def charmhub_tracks(self) -> List[str]:
        """Return the list of tracks defined in charmhub for the project.

        This returns the tracks that have been defined in the charmhub, minus
        the risk. e.g. yoga, if a full channel is yoga/stable.
        """
        if self._charmhub_tracks is None:
            store = get_store_client()
            channels = store.list_releases(self.charmhub_name)[1]
            tracks = collections.OrderedDict()
            for channel in channels:
                try:
                    tracks[channel.track]
                except KeyError:
                    tracks[channel.track] = 1
            self._charmhub_tracks = list(tracks.keys())
        return self._charmhub_tracks

    @property
    def channels(self) -> Set[CharmChannel]:
        if not self._channels:
            self._channels = set()
            for value in self.branches.values():
                for channel in value['channels']:
                    self._channels.add(CharmChannel(self, channel))

        return self._channels

    @property
    def tracks(self) -> Set[str]:
        return set([c.track for c in self.channels])

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
        any_changes = (any(not (r['exists']) or r['changed']
                           for r in current['in_config_recipes'].values()))
        if not (any_changes) and not (current['non_config_recipes']):
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
            elif not (state['exists']):
                if dry_run:
                    print(f'Would create recipe {recipe_name} (dry_run)')
                else:
                    print(f'Creating charm recipe for {recipe_name} ...',
                          end='')
                    build_from = state['build_from']
                    lp_recipe = self.lpt.create_charm_recipe(
                        recipe_name=recipe_name,
                        branch_info=build_from['branch_info'],
                        lp_branch=build_from['lp_branch'],
                        owner=self.lp_team,
                        project=self.lp_project,
                        store_name=self.charmhub_name,
                        channels=build_from['channels'])
                    print('done')

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
            for lp_branch in self.lp_repo.branches:  # type: ignore
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
                    tracks = ((self._encode_track_name(channels), channels), )
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

                    # Now if filtering just continue
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
        any_changes = (any(not (r['exists']) or r['changed']
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
                    if not (detail_['exists']):
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
                    bases = self._get_bases_from_config(branch)
                    channels = ', '.join(detail['current_recipe']
                                         .store_channels)
                    print(f"   - {name[:40]:40} - "
                          f"git branch: {branch[:20]:20} "
                          f"channels: {channels:30}"
                          f"bases: {','.join(bases or [])}",
                          file=file)

    def get_builds(self,
                   channels: Set[str] = None,
                   arch_tag: str = None,
                   detect_error: bool = False
                   ) -> Generator[Tuple[TypeLPObject, TypeLPObject],
                                  None, None]:
        """Get the builds associated to a charm.

        This method yields a tuple with the recipe and the build objects.

        :param channels: filter list of builds by a set of channels (e.g.
                         'foo/edge', 'latest/edge')
        :param arch_tag: filter list of build by architecture (e.g. 'amd64')
        :param detect_error: Attempt to found errors in the building log when
                             the built was not successful.
        :returns: a generator with the all builds found.
        """
        lp_recipes = self.lpt.get_charm_recipes(self.lp_team, self.lp_project)
        builds = collections.defaultdict(dict)
        for recipe in sorted(lp_recipes, key=lambda x: x.name):
            if (channels and
                    not channels.intersection(set(recipe.store_channels))):
                logger.debug((f'Skipping recipe {recipe.name}, because '
                              f'"{channels}" not in {recipe.store_channels}'))
                continue

            logger.debug(f'Getting builds for recipe {recipe.name}')
            # single revision will generate one or more builds, the list of
            # builds returned by LP is sorted in descending order, so we only
            # care about the first revision of the list and all the builds
            # associated with that revision, once a new revision shows up, we
            # know they are old builds and have been superseded by newer
            # commits, so we can short circuit the loop.
            _revision = None
            for build in recipe.builds:
                build_arch_tag = build.distro_arch_series.architecture_tag
                if arch_tag and arch_tag != build_arch_tag:
                    logger.debug((f'Skipping build of arch {build_arch_tag} '
                                  f'of recipe {recipe.name}'))
                    continue

                series_arch = f'{build.distro_series.name}/{build_arch_tag}'
                logger.info(
                    'Found build of %s for %s in %s (%s)',
                    recipe.name, series_arch, recipe.store_channels,
                    build.revision_id[:7] if build.revision_id else None
                )
                date = build.datebuilt
                if _revision and _revision != build.revision_id:
                    logger.debug(
                        'Breaking loop, because revision changed (%s != %s)',
                        _revision, build.revision_id
                    )
                    break
                _revision = build.revision_id
                if (series_arch not in builds[recipe.name] or
                        (date and
                         builds[recipe.name][series_arch]['datebuilt'] < date
                         )):
                    yield recipe, build

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
                if not (current_recipe.can_upload_to_store) or force:
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
            macaroon_dict = json.loads(
                recipe.beginAuthorization())  # type:ignore
            result = authorize_from_macaroon_dict(macaroon_dict)
            recipe.completeAuthorization(
                discharge_macaroon=result)  # type:ignore
        # blanket catch.  This is part of serveral attempts, so we don't want
        # to stop trying just because one fails.  If all fail, it'll be pretty
        # obvious!
        except Exception as e:
            logger.error(
                "Failed authenticating for upload.  Recipe: %s "
                "Reason: %s", recipe.name, str(e))

    def request_build_by_branch(self,
                                branches: List[str],
                                force: bool = False,
                                dry_run: bool = False) -> List[object]:
        """Request a build for the recipe(s) associated to the git branch(es).

        :param branches: a list of branches to match to find the recipes.
        :param force: if True, the build is requested even if there is a valid
                      build.
        :param dry_run: if True, the request for building is logged, but not
                        submitted to Launchpad.
        :returns: list of builds
        """
        builds = []
        for recipe in self._find_recipes(branches):

            build = self.request_build_by_recipe(recipe, force, dry_run)
            if build:
                builds.append(build)
                print(f'{self.charmhub_name}: New build requested '
                      f'{build.web_link}')

        return builds

    def request_build_by_recipe(self,
                                recipe: TypeLPObject,
                                force: bool,
                                dry_run: bool) -> Optional[TypeLPObject]:
        """Request a build for the recipe.

        :param recipe: recipe to request the build for.
        :param force: if True, no checks are made to detect if the build is
                      needed.
        :param dry_run: if True, the request for building is printed, but not
                        submitted to Launchpad.
        :returns: the build object or None when no build was requested.
        """
        build = None
        do_build = False
        if force:
            logger.debug('Forcing build of recipe %s', recipe)
            do_build = True
        elif not self.is_build_valid(recipe.builds[0]):
            logger.debug('Build %s is no valid, so request new build',
                         recipe.builds[0])
            do_build = True

        if do_build:
            if dry_run:
                print(f'{self.charmhub_name}: New build needed (dry-run)')
            else:
                # the recipe build channels need to be passed when requesting
                # the build, otherwise the build is made without the overrides
                # that the recipe has defined.
                build = recipe.requestBuilds(
                    channels=recipe.auto_build_channels
                )
        else:
            logger.debug('Not required to build: %s', recipe)

        return build

    def is_build_valid(self, build: TypeLPObject) -> bool:
        """Determine if the build is valid.

        A valid build a build that meets the following criteria:
        - it's associated recipe has the attribute can_upload_to_store set to
          True, but the build has no store_upload_revision set, and the build
          is not in progress (states: currently build, uploading build or needs
          building)
        - the build state is in any of: 'Failed to build', 'Failed to upload'.
        - the associated recipe is stale.

        :param build: build to check if it is valid.
        :returns: True if the last build is valid.
        """
        logger.debug(('Recipe can_upload_to_store %s , '
                      'store_upload_revision %s, build state %s, is stale %s'),
                     build.recipe.can_upload_to_store,
                     build.store_upload_revision,
                     build.buildstate, build.recipe.is_stale)
        if (build.recipe.can_upload_to_store and
                not build.store_upload_revision and
                build.buildstate not in [CURRENTLY_BUILDING,
                                         UPLOADING_BUILD,
                                         NEEDS_BUILDING]):
            return False
        elif build.buildstate in [FAILED_TO_BUILD, FAILED_TO_UPLOAD]:
            return False
        elif build.recipe.is_stale:
            return False

        return True

    def request_code_import(self,
                            dry_run: bool):
        """Request a new code import on Launchpad.

        :param dry_run: if True, the request for building is printed, but not
                        submitted to Launchpad.
        """
        if dry_run:
            print(f'Requesting new code import {self.lp_repo} (dry-run)')
            return

        self.lp_repo.code_import.requestImport()

    def copy_channel(self,
                     source: CharmChannel,
                     destination: CharmChannel,
                     bases: List[str],
                     ignore_arches: Optional[List[str]] = None,
                     dry_run: bool = True,
                     force: bool = False,
                     retries: int = 0) -> Set[int]:
        """Copy the published charms from one channel to another.

        Note: existing released revisions on a channel may not have had the
        resources appropriately assigned; if they are not specified in the
        release command, then it will fail.  Therefore, this command works
        really hard to find the resources for a revision (and then the charm's
        metadata) and assign a resource:revision to it as needed.

        :param source: the source channel
        :param destination: the destination channel
        :param bases: Filter by base (e.g. '20.04', '22.04', etc)
        :param ignore_arches: Filter out arches that are not wanted in the
            copy.
        :param dry_run: if True it won't commit the operation
        :param retries: Retry if charmhub responds with a 500 error.
        :returns: the list of revisions that have been copied.
        """
        copied_revisions = set()
        revisions = source.get_revisions_for_bases(
            bases, ignore_arches=ignore_arches)
        target_revisions = destination.get_revisions_for_bases(
            bases, ignore_arches=ignore_arches)
        self.log.info("Source revisions: %s, target revisions: %s",
                      ', '.join(str(r) for r in revisions),
                      ', '.join(str(r) for r in target_revisions))
        metadata = source.get_charm_metadata_for_channel()
        resource_names = source.get_resources_from_metadata(metadata or {})
        for revision in revisions:
            if revision in target_revisions:
                if not force:
                    self.log.info(
                        "Revision %s already released in channel %s",
                        revision, destination.name)
                    continue
                else:
                    self.log.info(
                        "Revision %s already in channel %s but force enabled "
                        "so releasing anyway.",
                        revision, destination.name)
            self.log.info('Releasing %s revision %s into channel %s',
                          self.charmhub_name,
                          revision,
                          destination.name)
            destination.release(revision,
                                dry_run=dry_run,
                                retries=retries,
                                resource_names=resource_names)
            copied_revisions.add(revision)

        return copied_revisions

    def clean_channel(self,
                      source: CharmChannel,
                      bases: List[str],
                      ignore_arches: Optional[List[str]] = None,
                      dry_run: bool = True,
                      retries: int = 0) -> Set[int]:
        """Clean the channel and keep revisions based on the bases passed.

        Note: existing released revisions on a channel may not have had the
        resources appropriately assigned; if they are not specified in the
        release command, then it will fail.  Therefore, this command works
        really hard to find the resources for a revision (and then the charm's
        metadata) and assign a resource:revision to it as needed.

        If the target channel clean command fails, then it is ignored (no
        release is done), and if the revisions on the channel don't change,
        then no clean or release is done.

        :param source: the channel to clean
        :param bases: Filter by base (e.g. '20.04', '22.04', etc)
        :param dry_run: if True it won't commit the operation
        :param retries: Retry if charmhub responds with a 500 error.
        """
        copied_revisions = set()
        all_revisions = source.get_all_revisions()
        self.log.debug("All revisions in channel: %s are %s",
                       source.name,
                       ",".join(str(r) for r in all_revisions))
        revisions = source.get_revisions_for_bases(
            bases, ignore_arches=ignore_arches)
        self.log.debug("Selected revisions in channel: %s (bases %s) are %s",
                       source.name,
                       ", ".join(bases),
                       ",".join(str(r) for r in revisions))
        if all_revisions == revisions:
            self.log.info("No need to clean channel: "
                          "Revisions %s for bases %s are all that is on "
                          "channel: %s",
                          ",".join(str(r) for r in list(sorted(revisions))),
                          ", ".join(bases),
                          source.name)
            return copied_revisions
        metadata = source.get_charm_metadata_for_channel()
        resource_names = source.get_resources_from_metadata(metadata or {})

        self.log.info('Closing %s: %s', self.charmhub_name, source.name)
        try:
            source.close(dry_run=dry_run, retries=retries)
        except Exception:
            # if an exception is raised, the command failed, don't try to do
            # anything in this case.
            return copied_revisions
        for revision in revisions:
            self.log.info('Releasing %s revision %s into channel %s',
                          self.charmhub_name,
                          revision,
                          source.name)
            source.release(revision,
                           dry_run=dry_run,
                           retries=retries,
                           resource_names=resource_names)
            copied_revisions.add(revision)

        return copied_revisions

    def change_risk(self,
                    channel: CharmChannel,
                    to_risk: str,
                    branch: Optional[str] = None,
                    dry_run: bool = True,
                    retries: int = 0) -> Optional[Set[int]]:
        """Change the charm from one risk to another.

        This function finds the appropriate revisions (according to the bases
        configured - and checks that they are configured) and puts them on the
        target risk along with any required resources.

        Note this function raises an Exception if the bases are not configured,
        or the associated git branch, cannot be determined for the charm. This
        is a weakness of the current system.  Use the copy-channel command
        instead.

        The git branch is determined by either finding a unique exact match of
        the track/risk, and if that fails, and then trying, or an exact track
        match.

        :param channel: the channel to take the revision(s) from.
        :param to_risk: where to put the revisions.
        :param dry_run: if True, just print what would be done, rather than
            doing it.
        :param retries: Retry if charmhub responds with a 500 error.
        :returns: Set of revisions that were copied.
        :raises: AssertionError if the git branch couldn't be determined.
        """
        copied_revisions = set()

        # Determine the branch that matches the channel that is selected.
        if branch is None:
            branch = self._determine_repo_branch_from_channel(channel.name)
            if branch is None:
                if '/' in channel.name:
                    track = channel.name.split('/', 1)[0]
                    branch = self._determine_repo_branch_from_channel(track)
            assert branch is not None, "Couldn't determine branch from channel"

        # get the configured bases for the branch.
        branch_spec = self.branches.get(
            branch, self.branches.get(f"refs/heads/{branch}"))
        assert branch_spec is not None, "branch incorrectly specified!"
        bases = branch_spec.get('bases')
        assert bases is not None, "No bases specified for branch."
        self.log.debug("Found bases for '%s' as: %s",
                       channel.name, ','.join(bases))

        # copy the revisions as per the bases to the target risk.
        revisions = list(sorted(channel.get_revisions_for_bases(bases)))
        self.log.debug("Found revisions to copy as: %s",
                       ','.join(str(r) for r in revisions))
        if not revisions:
            self.log.info("No revisions found, so nothing to do")
            return

        metadata = channel.get_charm_metadata_for_channel()
        resource_names = channel.get_resources_from_metadata(metadata or {})

        # See if there are any duplicate-channels defined.
        duplicate_channel_names = branch_spec.get('duplicate-channels', [])
        duplicate_channels = [CharmChannel(channel.project,
                                           f"{c}/{channel.risk}")
                              for c in duplicate_channel_names]
        if duplicate_channels:
            self.log.info("Channels to sync (duplicate-channels): %s",
                          ','.join(str(c) for c in duplicate_channels))
            # check revisions are on the duplicate channels, if not, release
            # them there
            for c in duplicate_channels:
                c_revisions = c.get_revisions_for_bases(bases)
                self.log.info("Revisions %s found on %s",
                              ','.join(str(r) for r in c_revisions),
                              c)
                for revision in revisions:
                    if revision not in c_revisions:
                        self.log.info(
                            'Releasing %s revision %s into channel %s',
                            self.charmhub_name,
                            revision,
                            c.name)
                        c.release(revision, dry_run=dry_run, retries=retries,
                                  resource_names=resource_names)

        # now for all possible channels, release the revisions into the
        # appropriate channel.
        for src_channel in [channel] + duplicate_channels:
            # now form destination channel, for the release.
            destination = f"{src_channel.track}/{to_risk}"
            destination_channel = CharmChannel(channel.project, destination)

            # get the revisions currently on the destination channel.
            destination_revisions = (destination_channel
                                     .get_revisions_for_bases(bases))
            self.log.info("Revisions found for %s: %s",
                          destination_channel.name,
                          ", ".join(str(r) for r in destination_revisions))

            for revision in revisions:
                if revision in destination_revisions:
                    self.log.info(
                        "Revision %s already existing in channel %s.",
                        revision,
                        destination_channel.name)
                    continue
                self.log.info('Releasing %s revision %s into channel %s',
                              self.charmhub_name,
                              revision,
                              destination_channel.name)
                destination_channel.release(revision,
                                            dry_run=dry_run,
                                            retries=retries,
                                            resource_names=resource_names)
                copied_revisions.add(revision)

        return copied_revisions

    def repair_resource(self,
                        channel: CharmChannel,
                        bases: List[str],
                        dry_run: bool = True,
                        check: bool = True,
                        retries: int = 0) -> Optional[Set[int]]:
        """Repair the revisions on the channel by releasing with resources.

        This function finds all of the revisions on a particular channel, and
        optionally filtered by the bases and ensures that all of the revisions
        are available on the that release.  If not then it attempts to identify
        the resources (and there versions) using the metadata.yaml from the
        same branch as the charm (note that this may be too new for the actual
        revision released) and then re-release the charm with that revision.

        The git branch is determined by either finding a unique exact match of
        the track/risk, and if that fails, and then trying, or an exact track
        match.

        :param channel: the channel to take the revision(s) from.
        :param bases: filter the revisions according to bases.
        :param dry_run: if True, just print what would be done, rather than
            doing it.
        :param retries: Retry if charmhub responds with a 500 error.
        :returns: Set of revisions that were copied.
        :raises: AssertionError if the git branch couldn't be determined.
        """
        # firstly, determine the git branch associated with the channel
        branch = self._determine_repo_branch_from_channel(channel.name)
        if branch is None:
            if '/' in channel.name:
                track = channel.name.split('/', 1)[0]
                branch = self._determine_repo_branch_from_channel(track)
        if branch is None:
            self.log.info("Couldn't determine the repo branch for %s on %s",
                          channel.name, self.charmhub_name)
            return

        # if bases are empty, get them from the config if available.
        if not bases:
            bases = self._get_bases_from_config(branch)
            if bases is None:
                self.log.info("No bases for channel %s, so ignoring.",
                              self.name)
                return
            self.log.debug(
                "Found bases for '%s' as: %s", channel.name, ','.join(bases))

        # copy the revisions as per the bases to the target risk.
        revisions = list(sorted(channel.get_revisions_for_bases(bases)))
        self.log.info("Found revisions to potentially fix as: %s",
                      ','.join(str(r) for r in revisions))
        if not revisions:
            self.log.info("No revisions found, so nothing to do")
            return

        metadata = channel.get_charm_metadata_for_channel()
        if metadata is None:
            self.log.info("Couldn't get medtadata, so can't repair this "
                          "channel: %s", channel.name)
            return
        resource_names = channel.get_resources_from_metadata(metadata or {})

        # for each revision found, we resolve the resources and then see
        # whether the revision needs to be re-released on the channel.
        for revision in revisions:
            (found_resources, missing_resources) = (
                channel._resolve_resources_for_revision(
                    revision, resource_names))
            if not missing_resources:
                self.log.info("All resources present, so not updating: %s",
                              ', '.join(r[0] for r in found_resources))
                continue

            # re-release the revision on the channel with the resources
            cmd = ['charmcraft', 'release', self.charmhub_name,
                   f'--revision={revision}', f'--channel={channel.name}']
            for resource in found_resources + missing_resources:
                cmd.append(f'--resource={resource[0]}:{resource[1]}')
            if dry_run:
                print(' '.join(cmd), " # dry-run mode")
            else:
                print(f"Running: {' '.join(cmd)}")
                run_charmcraft(cmd, check=check, retries=retries)

    def _get_bases_from_config(self, branch: str) -> List[str]:
        """Get the 'bases' config if it exists from builder config."""
        # get the configured bases for the branch.
        branch_spec = self.branches.get(
            branch, self.branches.get(f"refs/heads/{branch}"))
        assert branch_spec is not None, "branch incorrectly specified!"
        bases = branch_spec.get('bases')  # type:ignore
        if bases is None:
            return []
        return bases

    def _determine_repo_branch_from_channel(
            self, channel: str
    ) -> Optional[str]:
        """Determine, if possible, the repo branch for a channel.

        The channel can be track/risk or just 'track'.  If it is not found, or
        not unique, then None will be returned.  It is the prefix of the
        channel that is searched against.

        :param channel: the track[/risk] against which to match against.
        :returns: None if not found, otherwise the branch (e.g. master,
            stable/x)
        """
        found_branches = set()
        len_channel = len(channel)
        for _branch, branch_info in self.branches.items():
            if _branch.startswith('refs/heads/'):
                branch = _branch[len('refs/heads/'):]
            else:
                branch = _branch
            for _channel in branch_info.get('channels', []):
                if channel == _channel[:len_channel]:
                    found_branches.add(branch)

        if len(found_branches) == 1:
            return list(found_branches)[0]
        return None

    def _find_recipes(self, branches):
        info = self._calc_recipes_for_repo()
        for in_config_recipe in info['in_config_recipes'].values():
            branch_path = (
                in_config_recipe['build_from']['lp_branch'].path or '')
            if branch_path.startswith('refs/heads/'):
                branch_path = branch_path[len('refs/heads/'):]
            if branches and (branch_path not in branches):
                logger.info("Ignoring branch: %s as not in branches match.",
                            branch_path)
                continue
            current_recipe = in_config_recipe['current_recipe']
            if current_recipe is not None:
                logger.debug('Found recipe: %s', current_recipe.web_link)
                yield current_recipe

    @staticmethod
    def _encode_track_name(channels: List[str]) -> str:
        """Decode a list of channels into a track name.

        The track name needs to be compatible with the track name from former
        _group_channels() function (now deleted) which was used to group
        channels to the same track. The track name is the first of the channels
        found. If two tracks are found, then the track-name is a hyphenated
        pair of the tracks.  If 3 or more are found, then it is the first
        track, two hyphens and the final track name.

        :param channels: The list of track/risk channel descriptors.
        :returns: a string representing the track name.
        """
        tracks = []
        for channel in channels:
            if '/' in channel:
                track, _ = channel.split('/', 1)
            else:
                track = channel
            if track not in tracks:
                tracks.append(track)
        # now choose the track name.
        num = len(tracks)
        if num == 0:
            return 'unknown'
        elif num == 1:
            return tracks[0]
        elif num == 2:
            return f"{tracks[0]}-{tracks[1]}"
        else:
            return f"{tracks[0]}--{tracks[-1]}"

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
            if spec.get('bases'):
                bases_str = f" [bases: {','.join(spec.get('bases'))}]"
            else:
                bases_str = ""
            branches.append(f"{bname} -> {channels}{bases_str}")
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
