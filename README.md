# Charmhub LP Tools

Tools to configure and manage repositories and Launchpad builders.

This repository provides the `charmhub-lp-tool` command that provides the
ability to configure and manage the launchpad builders, repositories and
branches in repositories.

The commands are:

* `show`: The "show" command shows the current configuration for the charm
  recipes as defined in launchpad.
* `list`: List the charms defined in the configuration passed.
* `diff`: Diff the declared config with the actual config in launchpad. This
  shows the config and highlights missing or extra configuration that is in
  launchpad. Note that git repositories can have extra branches and these are
  not seen in the diff. Missing branches that are in the config are
  highlighted.
* `sync`: Sync the config to launchpad. Effectively, this takes the diff and
  applies it to the projects, creating or updating recipes as required.
* `delete`: Delete a recipe from launchpad based on a track/risk. e.g. use
  `--track` latest `--risk` edge to remove the recipe that pushes to the
  latest/stable track. Note it does not remove the revision from the
  charmhub. This is purely managing the recipes in launchpad.
* `check-builds`: Check the state of the builds available at Launchpad.
* `authorize`: Authorize helper to authorize the launchpad recipes to upload
  to the charmhub. Each recipe needs authorization, and this helper will use
  the same filters used to select the project group, charms, ignored charms,
  and branch to select the charm recipes that need authorizing. The Charmhub
  user that can upload charms will need to be logged in. This is a different
  user account than Launchpad.
* `request-build`: Request the building of recipes on Launchpad, a check is made
  on the client side to determine if a new build is really needed, unless
  `--force` is passed.


Note that `sync` requires the `--i-really-mean-this` flag as it is potentially
destructive.  `sync` also has other flags.

As always, use the `-h|--help` on the command to discover what the options are.

## Installation

### pip

Installing from a local copy:

```
pip3 install .
```

Installing from the git repository:

```
pip3 install "git+https://github.com/openstack-charmers/charmhub-lp-tools.git#egg=charmhub-lp-tools
```

### Snap (recommended) ![snap version](https://badgen.net/snapcraft/v/charmhub-lp-tool)

```
sudo snap install --edge charmhub-lp-tool
sudo snap connect charmhub-lp-tool:password-manager-service
```

> **_Note:_** the `password-manager-service` interface is used to store the
> Launchpad access token.

## Configuration

charmhub-lp-tool reads the configuration file from the following locations:

* `${XDG_CONFIG_HOME}/.config/charmhub_lp_tools/config.yaml`
* `${HOME}/.config/charmhub_lp_tools/config.yaml`

Example configuration file:

``` yaml
config_dir: /home/ubuntu/charmed-openstack-info/charmed_openstack_info/data/lp-builder-config/
log_level: WARNING
```

### Options

| Key        | Description                   |
|------------|-------------------------------|
| config_dir | Path to the lp-builder-config |
| log_level  | Default log level             |
