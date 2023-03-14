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
    Regex,
    Schema,
)

_CHARMHUB_NAME = Regex(r'^[a-z][a-z0-9_\-]+$')
_LP_NAME = Regex(r'^[a-z][a-z0-9_\-]+$')


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
            },
        },
    }],
})
