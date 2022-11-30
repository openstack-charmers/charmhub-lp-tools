from enum import Enum


class Risk(Enum):
    """Charmhub risk levels."""
    EDGE = 'edge'
    BETA = 'beta'
    CANDIDATE = 'candidate'
    STABLE = 'stable'


LIST_OF_RISKS = [x.value for x in list(Risk)]
