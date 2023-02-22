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

import configparser
import logging
import os

import git

from .exceptions import BranchNotFound

logger = logging.getLogger(__name__)


def get_branch_name(git_repo: git.Repo) -> str:
    """Get branch name from gitreview file.

    The branch name defined in the gitreview file in the 'defaultbranch'
    section is used, if it's not set, then DEFAULT_BRANCH_MASTER is returned.

    :param git_repo: the repo to operate on.
    """
    config = get_gitreview(git_repo)
    try:
        return config['gerrit']['defaultbranch']
    except KeyError:
        return get_default_branch_name(git_repo)


def get_gitreview(git_repo: git.Repo) -> configparser.ConfigParser:
    """Get gitreview file content.

    :param git_repo: the repo to operate on.
    :returns: the content of the gitreview file parsed.
    """
    config = configparser.ConfigParser()
    git_root = git_repo.git.rev_parse('--show-toplevel')
    config.read(os.path.join(git_root, '.gitreview'))
    return config


def get_default_branch_name(git_repo: git.Repo) -> str:
    """Retrieve the default branch name.

    Identify if the repository uses 'master' or 'main' as default branch name.

    :raises BranchNotFound: when there is no main nor master branches defined
    :returns: the default branch name
    """
    # default branches are something defined by the remote, it can be
    # retrieved with "git remote show $REMOTE | grep 'HEAD branch' ", although
    # the issue with it is that we would be at the mercy of the remote's name
    # which typicall is 'origin', but it's not guaranteed, since in github is
    # common to use 'upstream', so we'll go for a simpler and more reliable
    # approach, first to check if 'main' branch exists and return that,
    # otherwise return 'master', if none of those branches exist locally just
    # raise an exception.
    branch_names = [ref.name for ref in git_repo.references]
    if 'main' in branch_names:
        return 'main'
    elif 'master' in branch_names:
        return 'master'
    else:
        msg = "No main nor master branch found, branches found: %s"
        raise BranchNotFound(msg % branch_names)
