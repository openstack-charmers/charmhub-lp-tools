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
"""Helpers to parse different strings with known formats."""

from typing import Tuple

from charmhub_lp_tools.constants import (
    LIST_OF_RISKS,
    Risk,
)
from charmhub_lp_tools.exceptions import InvalidRiskLevel


def parse_channel(value: str) -> Tuple[str, str]:
    """Parse a string that represents a channel.

    :param value: a string that represents a channel.
    :returns: a tuple with track and risk.
    """
    if value in LIST_OF_RISKS:
        # this is a risk-only value, we assume 'latest' track
        return ('latest', value)

    try:
        # track/risk
        (track, risk) = value.split('/')
        if risk not in LIST_OF_RISKS:
            raise InvalidRiskLevel(f'Invalid risk: {risk}')
        return (track, risk)
    except ValueError:
        pass

    if value not in LIST_OF_RISKS:
        # it's a track-only value, so we assume 'stable' risk.
        return (value, Risk.STABLE.value)

    raise ValueError('Could not parse %s' % value)
