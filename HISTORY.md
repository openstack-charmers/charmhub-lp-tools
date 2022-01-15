# Repository History

The initial commits in this repository were taken from two branches on the
release-tools repository.  These were, and are the same named (but obviously
different commit ids) as the commits in this repository.  This is just to
maintain the history of the project.

```
be9e66f [2022-01-07] (rt-ajkavanagh/charm-launchpad-builders-alex) Fix (most) typing issues in the charm-luanchpad-builders code [Alex Kavanagh]
323c0ae [2021-12-09] Add type hints to some properties in the CharmProject class. [Alex Kavanagh]
14322f0 [2021-12-09] Refactor CharmProject class into its own submodule [Alex Kavanagh]
bd89887 [2021-12-09] Add logging config to lpt submodule. [Alex Kavanagh]
f9b5ad9 [2021-12-09] Refactor the LaunchpadTools class into it's own module [Alex Kavanagh]
ce585ef [2021-12-09] Remove config command stubs [Alex Kavanagh]
7a44fc0 [2021-12-09] Add command to show currently configured LP recipes [Alex Kavanagh]
9ca3d78 [2021-12-09] Refactor LaunchpadTools as a member in CharmProject [Alex Kavanagh]
f20c9c7 [2021-12-08] Implement -c, --charm option to select a charm/charms [Alex Kavanagh]
95e72f4 [2021-12-08] Add 'diff' command [Alex Kavanagh]
807e4bd [2021-12-07] Refactor getting git repository into a function [Alex Kavanagh]
fef2201 [2021-12-07] Type the objects returned by launchpadlib [Alex Kavanagh]
384c299 [2021-12-07] Add list command to show which charms are in the config [Alex Kavanagh]
6d90695 [2021-12-06] Add in subcommands for showing / working with recipes [Alex Kavanagh]
adde6c2 [2021-12-06] Refactor setup_logging/parse_args to be closer to main [Alex Kavanagh]
c57a364 [2021-12-06] Refactor update_charm_recipe [Alex Kavanagh]
5afda7f [2021-12-06] Refactor ensure_* functions from LaunchpadTools to CharmProject [Alex Kavanagh]
163856e [2021-12-05] Fix some formatting issues [Alex Kavanagh]
cf45855 [2021-12-03] Add back in destructive functions after refactor [Alex Kavanagh]
6f7cf5c [2021-12-03] Remove misc comment that no longer applies [Alex Kavanagh]
1fce904 [2021-12-03] Introduce a GroupConfig object to hold all the configs [Alex Kavanagh]
6ca9c1e [2021-12-03] Refactor main() into separate functions [Alex Kavanagh]
178acaa [2021-12-03] Add --log option to script [Alex Kavanagh]
55188c7 [2021-12-03] Update configure_charm_recipe to build for multiple tracks [Alex Kavanagh]
46418aa [2021-12-03] Remove TODO comment [Alex Kavanagh]
129cc16 [2021-12-03] Add a group_channels() to LaunchpadTools [Alex Kavanagh]
d85942c [2021-12-03] Cleanup function 'update_charm_recipe' in LaunchpadTools [Alex Kavanagh]
80bf862 [2021-12-03] Add pprint to pretty-print the group config [Alex Kavanagh]
ab62fe1 [2021-12-03] Change looging in class LaunchpadTools [Alex Kavanagh]
3344f06 [2021-12-03] Modify typing import to reduce line noise [Alex Kavanagh]
6aef840 [2021-12-03] Change logger calls to be lazy in formatting [Alex Kavanagh]
f502267 [2021-12-03] Make the track part of a recipe delimited with a period [Alex Kavanagh]
0abef23 [2021-12-03] Add a __str__ method for CharmProject [Alex Kavanagh]
ab86d00 [2021-11-29] Add some more debug and fix a couple of minor bugs [Alex Kavanagh]
80d3709 [2021-11-29] Enable passing config-dir and debug in a virtualenv [Alex Kavanagh]
90e0a83 [2021-11-22] (rt-origin/charmhub-launchpad-builders) Set Project VCS to Git [Billy Olsen]
794e2db [2021-11-21] Initial commit for tools for launchpad charm builders [Billy Olsen]
```
