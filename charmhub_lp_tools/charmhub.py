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

# Tools to work with the charmhub.


import logging
import pprint
from typing import Dict, Optional

from macaroonbakery import bakery, httpbakery
from pymacaroons.serializers import JsonSerializer


logger = logging.getLogger(__name__)

# Singleton to represent a client to the charmhub
_charmhub_client: Optional[httpbakery.Client] = None


def setup_logging(loglevel: str) -> None:
    """Sets up some basic logging."""
    logger.setLevel(getattr(logging, loglevel, 'ERROR'))


def get_charmhub_client() -> httpbakery.Client:
    global _charmhub_client
    if _charmhub_client is None:
        _charmhub_client = httpbakery.Client()
    return _charmhub_client


def authorize_from_macaroon_dict(auth_data: Dict) -> str:
    """Authorize a macacroon represented as a dictionary.

    :param auth_data: the macaroon that needs authorizing on charmhub.
    :raises ValueError: if got more than one discharge macaroon
    """
    logger.debug("Authorizing Macaroon data:\n%s", pprint.pformat(auth_data))
    root = bakery.Macaroon.from_dict(auth_data)
    discharges = []
    for caveat in root.macaroon.caveats:
        if caveat.location not in (None, ""):
            discharges.append(get_charmhub_client().acquire_discharge(
                caveat, root.caveat_data.get(caveat.caveat_id)))
    if len(discharges) != 1:
        raise ValueError(
            "Expected one discharge macaroon, got: %s", len(discharges))
    result = discharges[0].macaroon.serialize(JsonSerializer())
    logger.debug("Result is:\n%s", pprint.pformat(result))
    return result
