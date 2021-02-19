# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2021 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Part crafting command line tool."""

import argparse
import logging
import sys

import yaml

import craft_parts
from craft_parts import ActionType, Step, errors


def main():
    """The main entry point."""

    options = _parse_arguments()

    if options.version:
        print(f"craft-parts {craft_parts.__version__}")
        sys.exit()

    logging.basicConfig(level=logging.INFO)

    with open(options.file) as f:
        part_data = yaml.safe_load(f)

    lf = craft_parts.LifecycleManager(part_data, application_name="craft-parts")

    try:
        command = options.command if options.command else "prime"
        if command == "clean":
            _do_clean(lf, options)
            sys.exit()

        _do_step(lf, options)
    except errors.InvalidPartName as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)


def _do_step(lf: craft_parts.LifecycleManager, options: argparse.Namespace) -> None:
    target_step = _parse_step(options.command) if options.command else Step.PRIME

    try:
        part_names = options.parts
    except AttributeError:
        part_names = []

    if options.update:
        lf.update()

    actions = lf.plan(target_step, part_names)

    if vars(options).get("plan_only"):
        printed = False
        for a in actions:
            if options.show_skipped or a.type != ActionType.SKIP:
                print(_action_message(a))
                printed = True
        if not printed:
            print("No actions to execute.")
        sys.exit()

    for a in actions:
        if vars(options).get("show_skipped") or a.type != ActionType.SKIP:
            print(f"Execute: {_action_message(a)}")
            lf.execute(a)


def _do_clean(lf: craft_parts.LifecycleManager, options: argparse.Namespace) -> None:
    if not options.parts:
        print("Clean all parts.")

    lf.clean(None, options.parts)


def _action_message(a: craft_parts.Action) -> str:
    msg = {
        Step.PULL: {
            ActionType.RUN: "Pull",
            ActionType.RERUN: "Repull",
            ActionType.SKIP: "Skip pull",
            ActionType.UPDATE: "Update sources for",
        },
        Step.BUILD: {
            ActionType.RUN: "Build",
            ActionType.RERUN: "Rebuild",
            ActionType.SKIP: "Skip build",
            ActionType.UPDATE: "Update build for",
        },
        Step.STAGE: {
            ActionType.RUN: "Stage",
            ActionType.RERUN: "Restage",
            ActionType.SKIP: "Skip stage",
        },
        Step.PRIME: {
            ActionType.RUN: "Prime",
            ActionType.RERUN: "Re-prime",
            ActionType.SKIP: "Skip prime",
        },
    }

    if a.reason:
        return f"{msg[a.step][a.type]} {a.part_name} ({a.reason})"

    return f"{msg[a.step][a.type]} {a.part_name}"


def _parse_step(name: str) -> Step:
    step_map = {
        "pull": Step.PULL,
        "build": Step.BUILD,
        "stage": Step.STAGE,
        "prime": Step.PRIME,
    }

    return step_map.get(name, Step.PRIME)


def _parse_arguments() -> argparse.Namespace:
    prog = "python -m craft_parts"
    description = (
        "A command line interface for the craft_parts module to build "
        "parts-based projects."
    )

    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "-f",
        "--file",
        metavar="filename",
        default="parts.yaml",
        help="The parts specification file (default: parts.yaml)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Refresh the stage packages list before procceeding",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Display the craft-parts version and exit",
    )

    subparsers = parser.add_subparsers(dest="command")

    step_parser = argparse.ArgumentParser(add_help=False)
    step_parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Show planned actions to be executed and exit",
    )
    step_parser.add_argument(
        "--show-skipped",
        action="store_true",
        help="Also display skipped actions",
    )

    pull_parser = subparsers.add_parser(
        "pull", parents=[step_parser], help="Pull the specified parts"
    )
    pull_parser.add_argument("parts", nargs="*", help="The list of parts to pull")

    build_parser = subparsers.add_parser(
        "build", parents=[step_parser], help="Build the specified parts"
    )
    build_parser.add_argument("parts", nargs="*", help="The list of parts to build")

    stage_parser = subparsers.add_parser(
        "stage", parents=[step_parser], help="Stage the specified parts"
    )
    stage_parser.add_argument("parts", nargs="*", help="The list of parts to stage")

    prime_parser = subparsers.add_parser(
        "prime", parents=[step_parser], help="Prime the specified parts"
    )
    prime_parser.add_argument("parts", nargs="*", help="The list of parts to prime")

    clean_parser = subparsers.add_parser(
        "clean", help="Clean the specified steps and parts"
    )
    clean_parser.add_argument(
        "parts", nargs="*", help="The list of parts whose this step should be cleaned"
    )

    return parser.parse_args()


if __name__ == "__main__":
    main()
