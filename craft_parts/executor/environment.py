# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2020-2021 Canonical Ltd
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

"""Helpers to handle part environment setting."""

import io
import logging
from typing import Dict

from craft_parts.infos import StepInfo
from craft_parts.parts import Part
from craft_parts.plugins import Plugin
from craft_parts.steps import Step
from craft_parts.utils import formatting_utils, os_utils

logger = logging.getLogger(__name__)


def generate_part_environment(
    *, part: Part, plugin: Plugin, step_info: StepInfo
) -> str:
    """Generate an environment suitable to run during a step.

    :returns: str with the build step environment.
    """
    # Craft parts' say.
    our_build_environment = _basic_environment_for_part(part=part, step_info=step_info)

    step = step_info.step

    # Plugin's say.
    if step == Step.BUILD:
        plugin_environment = plugin.get_build_environment()
    else:
        plugin_environment = dict()

    # Part's (user) say.
    user_build_environment = part.spec.build_environment
    if not user_build_environment:
        user_build_environment = []

    # Create the script.
    with io.StringIO() as run_environment:
        print("#!/bin/sh", file=run_environment)
        print("set -e", file=run_environment)

        print("# Environment", file=run_environment)

        print("## Part Environment", file=run_environment)
        for key, val in our_build_environment.items():
            print(f'export {key}="{val}"', file=run_environment)

        print("## Plugin Environment", file=run_environment)
        for key, val in plugin_environment.items():
            print(f'export {key}="{val}"', file=run_environment)

        print("## User Environment", file=run_environment)
        for env in user_build_environment:
            for key, val in env.items():
                print(f'export {key}="{val}"', file=run_environment)

        # Return something suitable for Runner.
        return run_environment.getvalue()


def _basic_environment_for_part(part: Part, *, step_info: StepInfo) -> Dict[str, str]:
    """Return the built-in part environment."""

    part_environment: Dict[str, str] = {}
    paths = [part.part_install_dir, part.stage_dir]

    bin_paths = list()
    for path in paths:
        bin_paths.extend(os_utils.get_bin_paths(root=path, existing_only=True))

    if bin_paths:
        bin_paths.append("$PATH")
        part_environment["PATH"] = formatting_utils.combine_paths(
            paths=bin_paths, prepend="", separator=":"
        )

    include_paths = list()
    for path in paths:
        include_paths.extend(
            os_utils.get_include_paths(root=path, arch_triplet=step_info.arch_triplet)
        )

    if include_paths:
        for envvar in ["CPPFLAGS", "CFLAGS", "CXXFLAGS"]:
            part_environment[envvar] = formatting_utils.combine_paths(
                paths=include_paths, prepend="-isystem", separator=" "
            )

    library_paths = list()
    for path in paths:
        library_paths.extend(
            os_utils.get_library_paths(root=path, arch_triplet=step_info.arch_triplet)
        )

    if library_paths:
        part_environment["LDFLAGS"] = formatting_utils.combine_paths(
            paths=library_paths, prepend="-L", separator=" "
        )

    pkg_config_paths = list()
    for path in paths:
        pkg_config_paths.extend(
            os_utils.get_pkg_config_paths(
                root=path, arch_triplet=step_info.arch_triplet
            )
        )

    if pkg_config_paths:
        part_environment["PKG_CONFIG_PATH"] = formatting_utils.combine_paths(
            pkg_config_paths, prepend="", separator=":"
        )

    return part_environment
