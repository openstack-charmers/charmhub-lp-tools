from enum import Enum


class Risk(Enum):
    """Charmhub risk levels."""
    EDGE = 'edge'
    BETA = 'beta'
    CANDIDATE = 'candidate'
    STABLE = 'stable'


LIST_OF_RISKS = [x.value for x in list(Risk)]
PROGRAM_NAME = 'openstack-charm-tools'
OSCI_YAML = 'osci.yaml'
DEFAULT_CHARMCRAFT_CHANNEL = '1.5/stable'

# tuple with LP auto build channel key and osci.yaml vars key
LIST_AUTO_BUILD_CHANNELS = [
    ('charmcraft', 'charmcraft_channel', DEFAULT_CHARMCRAFT_CHANNEL),
]
