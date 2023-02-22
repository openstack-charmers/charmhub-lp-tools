class CharmcraftError504(Exception):
    """charmcraft failed with a error 504 from charmhub."""


class InvalidRiskLevel(Exception):
    """Invalid risk level."""


class BranchNotFound(Exception):
    """Branch not found."""


class CharmNameNotFound(Exception):
    """Charm name not found declared in osci.yaml"""
