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
