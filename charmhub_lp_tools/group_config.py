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

import collections
import logging
import os
import pathlib
import pprint

import yaml

from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Optional,
)

from .charm_project import (
    CharmProject,
)
from .schema import config_schema
from .launchpadtools import (
    LaunchpadTools,
)


logger = logging.getLogger(__name__)


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
