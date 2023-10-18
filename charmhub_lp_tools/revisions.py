# Copyright 2023 Canonical

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

# http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Implement the print-revisions command."""

import argparse
import json
import logging
import os
import pathlib
import textwrap
from typing import (
    Any,
    Dict,
    List,
    Set,
)

from prettytable import PrettyTable

from .charm_project import (
    CharmChannel,
)

from .group_config import GroupConfig
from .parsers import (
    parse_channel,
)


logger = logging.getLogger(__name__)


def setup_parser(
    subparser: argparse.ArgumentParser
) -> argparse.ArgumentParser:
    """Setup parser for print-revisions command."""
    parser = subparser.add_parser(
        'print-revisions',
        help=('Print all the revisions for a base, arch, channel or any '
              'combination thereof.'),
    )
    parser.add_argument(
        '-s', '--channel',
        dest='channel',
        metavar='CHANNEL',
        required=True,
        help='The channel as track/risk to clean.',
    )
    parser.add_argument(
        '-b', '--base',
        dest='bases',
        action='append',
        type=str,
        help='Restrict to a particular base. Can be repeated.')
    parser.add_argument(
        '-a', '--arch',
        dest='arches',
        action='append',
        type=str,
        help='Restrict to a particular arch. Can be repeated.')
    parser.add_argument(
        '--format',
        dest='format',
        # choices=('table', 'json', 'html'),
        choices=('table', 'json', 'rst'),
        default='table',
        type=str.lower,
        help=('The format to output the report in. default is "table"'))
    parser.add_argument(
        '--tab-size',
        dest='tab_size',
        default=5,
        type=int,
        help=('The number of tabs in a tab group for RST format. Ignored for'
              'other formats.'))
    parser.add_argument(
        '-o', '--out',
        dest='output',
        help=('Write the output to a file.  Default is STDOUT'))

    parser.set_defaults(func=print_revisions)
    return parser


def get_revisions(channel: CharmChannel,
                  args_bases: List[str] | None,
                  args_arches: List[str] | None
                  ) -> Dict[str, Dict[str, List[int]]]:
    """Get the revisions by base -> arch -> [revisions].

    :param channel: the charm channel to work against.
    :param bases: Optional list of bases to restrict report to.
    :param arches: Optional list of arches to restrict report to.
    :returns: a mapping of base -> arch -> List of revisions
    """
    revisions: Dict[str, Dict[str, Set[int]]] = {}
    logger.debug("channel:  %s", channel)
    logger.debug("bases: %s", channel.bases)
    bases = sorted(set(channel.bases) & set(args_bases or channel.bases))
    logger.debug("selected bases: %s", bases)
    # now get the revisions for each of the bases found; in theory these
    # groups of revisions will be in 'increasing' numbers if they are
    # released for the charm.  It will also have to be done by architecture
    # as those may have different revision numbers.
    for base in bases:
        logger.debug("Looking at base %s", base)
        revisions[base] = channel.get_all_revisions_for_bases_by_arch(
            [base])
    logger.debug("All revisions found: %s", revisions)
    # now sort the revisions into base -> arch -> revision.  The arch is in
    # the form of '<arch>/<base>' and <arch> can be all.
    # First extract all the arches.
    arches_set: Set[str] = set(arch.split('/')[0]
                               for arch_revisions in revisions.values()
                               for arch in arch_revisions.keys())
    logger.debug("Architectures set: %s", arches_set)
    arches = sorted(arches_set & set(args_arches or arches_set))
    logger.debug("Filtered archectectures: %s", arches)
    # now assemble the resultant data.
    results: Dict[str, Dict[str, List[int]]] = {}
    for base in bases:
        results[base] = {}
        keys = revisions[base].keys()
        for arch in arches:
            for k in keys:
                if k.split('/')[0] == arch:
                    results[base][arch] = sorted(revisions[base][k])
    return results


def format_as_table(channel: CharmChannel,
                    results: Dict[str, Dict[str, List[int]]]) -> str:
    """Format the output as a PrettyTable.

    Note that the table is in the form:

          : base1 : base2 : ...
    arch1 : r1    : r1    : ...
    arch2 : r3    : r2    : ...

    i.e. bases across, arches down.

    :param results: the results to format into a table.
    :returns: the formatted string
    """
    bases = list(results.keys())
    if not bases:
        return ""
    t = PrettyTable()
    arch_headings = sorted(
        set(arch for arches in results.values() for arch in arches.keys()))
    t.field_names = [''] + bases
    t.align = 'l'  # align to the left.
    t.title = f'Charm: {channel.project.charmhub_name} - Track: {channel.name}'
    for arch_heading in arch_headings:
        row = [arch_heading]
        for base, arches in results.items():
            try:
                row.append(", ".join(str(a) for a in arches[arch_heading]))
            except KeyError:
                row.append("-")
        t.add_row(row)
    return t.get_string()


def format_as_rst(
        channel: CharmChannel,
        results: Dict[str, Dict[str, List[int]]],
        indent_level: int = 0) -> str:
    """Format the output as an ReST table (sphinx)

    This produces the table element with a group tab and then the included
    rows.

    :param results: the results to format into a table.
    :returns: the formatted string
    """
    rst_channel = format_channel_as_rst(results,indent_level=indent_level + 1)
    if not rst_channel:
        return ""
    return "\n".join([
        format_rst_grouptab(channel.project.charmhub_name,
                            indent_level=indent_level),
        rst_channel
    ])


def format_channel_as_rst(
        results: Dict[str, Dict[str, List[int]]],
        indent_level: int = 0) -> str:
    """Format the output as an ReST table (sphinx)

    This produces the table element; it'll need a header and group tabs created
    by another section.

    :param results: the results to format into a table.
    :returns: the formatted string
    """
    bases = list(results.keys())
    if not bases:
        return ""
    output: List[str] = [format_rst_header(indent_level,
                                           num_cols=len(bases) + 1)]
    # add the header.
    arch_headings = sorted(
        set(arch for arches in results.values() for arch in arches.keys()))
    output.append(format_rst_row([''] + bases,
                                 len(bases) + 1,
                                 indent_level=indent_level + 1))
    for arch_heading in arch_headings:
        row = [arch_heading]
        for base, arches in results.items():
            try:
                row.append(", ".join(str(a) for a in arches[arch_heading]))
            except KeyError:
                row.append('-')
        output.append(format_rst_row(row,
                                     len(row),
                                     indent_level=indent_level+1))
    return "\n".join(output)


def format_rst_tabs(indent_level: int=0) -> str:
    """Return the grouptab for an RST table.

    :returns: a string of lines, indented, for the tabs.
    """
    return textwrap.indent(
        "\n".join([
            f'.. tabs::',
            '',
        ]), '   ' * indent_level)


def format_rst_grouptab(header: str, indent_level: int=0) -> str:
    """Return the grouptab for an RST table.

    :param header: the string to put in the header.
    :returns: a string of lines, indented, for the grouptab.
    """
    return textwrap.indent(
        "\n".join([
            f'.. group-tab:: {header}',
            '',
        ]), '   ' * indent_level)


def format_rst_header(indent_level: int=0, num_cols=None) -> str:
    """Return the header for rst table.

    :param indent_level: how var (*3 spaces) to indent this header.
    :returns: a string of lines, indented, for the header.
    """
    if num_cols:
        widths = " ".join("1" * num_cols)
    else:
        widths = "auto"
    return textwrap.indent(
        "\n".join([
            '.. list-table::',
            '   :header-rows: 1',
            '   :widths: {}'.format(widths),
            '   :width: 75%',
            '   :stub-columns: 0',
            '',
        ]), '   ' * indent_level)


def format_rst_row(columns: List[str],
                   row_size: int,
                   empty_column: str = "",
                   indent_level: int = 0) -> str:
    """Format an rst row using columns.

    :param columns: a list of strings, one for each column.
    :param num_columns: the number of columns to populate.
    :param empty_column: the string to use for an empty column
    :param indent_level: the level (*3 space) to indent the section.
    :returns: a string of lines, indented, for the row.
    """

    try:
        lines: List[str] = ["* - {}".format(columns[0])]
    except IndexError:
        lines: List[str] = ["* - {}".format(empty_column)]
    lines: List[str] = []
    prefix = "* "
    for row in range(0, row_size):
        try:
            lines.append(f"{prefix}- {columns[row]}")
        except IndexError:
            lines.append(f"{prefix}- {empty_column}")
        prefix = "  "
    lines.append('')
    lines = [l.rstrip() for l in lines]
    return textwrap.indent("\n".join(lines), '   ' * indent_level)


def format_as_html(channel: CharmChannel,
                    results: Dict[str, Dict[str, List[int]]]) -> str:
    """Format the output as a HTML page.

    :param results: the results to format into a table.
    :returns: the formatted string
    """
    raise NotImplemented("HTML reports aren't implemented yet.")


def print_revisions(
    args: argparse.Namespace,
    gc: GroupConfig,
) -> None:
    """Entry point for the 'print-revisions' command."""
    logger.setLevel(getattr(logging, args.loglevel, 'ERROR'))
    track, risk = None, None
    try:
        track, risk = parse_channel(args.channel)
    except ValueError as e:
        logger.error(f"Malformed channel: {args.channel}: {e}")
        return
    output: List[str] = []

    for cp in gc.projects(select=args.charms):
        logger.debug("working with project: %s", cp)
        channel = CharmChannel(cp, args.channel)
        results = get_revisions(channel, args.bases, args.arches)
        if args.format == "table":
            output.append(format_as_table(channel, results))
        elif args.format == "json":
            output.append(json.dumps(results, indent=2))
        elif args.format == "rst":
            if (formatted := format_as_rst(channel, results, indent_level=1)):
                output.append(formatted)
                continue
        else:
            raise RuntimeError(
                f"Passed '{args.format}' which shouldn't be possible.")

    # if rst formatting then add tabs at args.tab_size increments.
    if args.format == 'rst':
        all_output = [format_rst_tabs()]
        for i, table in enumerate(output):
            if i > 0 and args.tab_size and (i % args.tab_size) == 0:
                all_output.append(format_rst_tabs())
            all_output.append(table)
        output = all_output

    if args.output:
        os.makedirs(pathlib.Path(args.output).parent, exist_ok=True)
        with open(args.output, "wt") as f:
            f.write("\n".join(output))
    else:
        print("\n".join(output))
