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

import os
import unittest

from unittest import mock

from charmhub_lp_tools import gitutils
from charmhub_lp_tools.exceptions import BranchNotFound


class TestGitUtils(unittest.TestCase):
    def setUp(self):
        self.fake_repo_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'fixtures/fake_repo/'))
        self.git_repo = mock.MagicMock()
        self.git_repo.git.rev_parse.return_value = self.fake_repo_path

    def test_get_branch_name(self):
        self.assertEqual(gitutils.get_branch_name(self.git_repo),
                         'stable/jammy')

    def test_get_gitreview(self):
        gitreview = gitutils.get_gitreview(self.git_repo)
        self.assertEqual(gitreview['gerrit']['host'], 'review.example.com')
        self.assertEqual(gitreview['gerrit']['port'], '1234')
        self.assertEqual(gitreview['gerrit']['project'], 'foo/charm-fake.git')
        self.assertEqual(gitreview['gerrit']['defaultbranch'], 'stable/jammy')

    def test_get_default_branch_name(self):
        main_branch = mock.MagicMock()
        main_branch.name = 'main'
        master_branch = mock.MagicMock()
        master_branch.name = 'master'
        jammy_branch = mock.MagicMock()
        jammy_branch.name = 'stable/jammy'

        self.git_repo.references = [jammy_branch]

        self.assertRaises(BranchNotFound,
                          gitutils.get_default_branch_name, self.git_repo)

        self.git_repo.references = [master_branch]
        self.assertEqual(gitutils.get_default_branch_name(self.git_repo),
                         'master')
        self.git_repo.references = [main_branch]
        self.assertEqual(gitutils.get_default_branch_name(self.git_repo),
                         'main')
        self.git_repo.references = [main_branch, master_branch]
        self.assertEqual(gitutils.get_default_branch_name(self.git_repo),
                         'main')
