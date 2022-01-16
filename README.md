# charmhub-lp-tools

Tools to configure and manage repositories and launchpad builders.

This repository provides the `charmhub-lp-tool` command that provides the
ability to configure and manage the launchpad builders, repositories and
branches in repositories.

The commands are:

* show -> display the current config.
* list -> show a list of the charms configured in the supplied config.
* diff -> show the current config and a diff to what is asked for.
* config -> show the asked for config
* sync -> sync the asked for config to the charm in the form of recipes.

Note that 'update' requires the --i-really-mean-this flag as it is potentially
destructive.  'update' also has other flags.

As always, use the -h|--help on the command to discover what the options are.

## Installation

Install into a venv using `pip install .`.  This will add the `charmhub-lp-tool` command.
