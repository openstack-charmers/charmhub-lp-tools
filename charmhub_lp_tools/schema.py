# Copyright 2023 Canonical
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Schema definition for lp-builder-config."""

from schema import (
    Optional,
    Or,
    Regex,
    Schema,
)

_CHARMHUB_NAME = Regex(r'^[a-z][a-z0-9_\-]+$')
_LP_NAME = Regex(r'^[a-z][a-z0-9_\-]+$')
_PROJECT_SERIES_STATUS = Or("Experimental",
                            "Active Development",
                            "Pre-release Freeze",
                            "Current Stable Release",
                            "Supported",
                            "Obsolete",
                            "Future",
                            only_one=True)

_SERIES_TITLE_DESC = "The product series title. Should be just a few words."
_SERIES_STATUS_DESC = ("Whether or not this series is stable and supported, "
                       "or under current development. This excludes series "
                       "which are experimental or obsolete.")
_SERIES_SUMMARY_DESC = ('A single paragraph that explains the goals of of '
                        'this series and the intended users. For example: '
                        '"The yoga series represents the current stable '
                        'series, and is recommended for all new deployments".')
DEFAULT_SERIES_STATUS = "Active Development"

config_schema = Schema({
    "defaults": {
        "team": str,
        Optional("branches"): {
            str: {
                Optional("build-channels"): dict,
                Optional("channels"): [str],
                Optional("enabled", default=True): bool,
                Optional("bases"): [str],
                Optional("duplicate-channels"): [str],
                Optional(
                    "series-title",
                    default=None,
                    description=_SERIES_TITLE_DESC
                ): str,
                Optional(
                    "series-summary",
                    default=None,
                    description=_SERIES_SUMMARY_DESC
                ): str,
                Optional("series-active", default=True): bool,
                Optional(
                    "series-status",
                    default=DEFAULT_SERIES_STATUS,
                    description=_SERIES_STATUS_DESC
                ): _PROJECT_SERIES_STATUS,
                Optional("auto-build", default=True): bool,
                Optional("upload", default=True): bool,
                Optional("recipe-name",
                         default='{project}.{branch}.{track}'): str,
            },
        },
    },
    "projects": [{
        "name": str,
        "charmhub": _CHARMHUB_NAME,
        "launchpad": _LP_NAME,
        "repository": str,
        Optional("team"): str,
        Optional("branches"): {
            str: {
                "build-channels": dict,
                "channels": [str],
                Optional("enabled", default=True): bool,
                Optional("bases"): [str],
                Optional("duplicate-channels"): [str],
                Optional(
                    "series-title",
                    default=None,
                    description=_SERIES_TITLE_DESC
                ): str,
                Optional(
                    "series-summary",
                    default=None,
                    description=_SERIES_SUMMARY_DESC
                ): str,
                Optional("series-active", default=True): bool,
                Optional(
                    "series-status",
                    default=DEFAULT_SERIES_STATUS,
                    description=_SERIES_STATUS_DESC
                ): _PROJECT_SERIES_STATUS,
                Optional("auto-build", default=True): bool,
                Optional("upload", default=True): bool,
                Optional("recipe-name",
                         default='{project}.{branch}.{track}'): str,
            },
        },
    }],
})
