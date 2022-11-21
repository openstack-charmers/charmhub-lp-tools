from enum import Enum


class Risks(Enum):
    EDGE = 'edge'
    BETA = 'beta'
    CANDIDATE = 'candidate'
    STABLE = 'stable'


LIST_OF_RISKS = [x.value for x in list(Risks)]
