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

"""Definitions and helpers for the action executor."""

import contextlib
import logging
import shutil
from typing import Dict, List

from craft_parts import callbacks, packages
from craft_parts.actions import Action, ActionType
from craft_parts.infos import PartInfo, ProjectInfo
from craft_parts.parts import Part, part_list_by_name
from craft_parts.schemas import Validator
from craft_parts.steps import Step

from .collisions import check_for_stage_collisions
from .part_handler import PartHandler

logger = logging.getLogger(__name__)


class Executor:
    """Execute lifecycle actions."""

    def __init__(
        self,
        *,
        part_list: List[Part],
        validator: Validator,
        project_info: ProjectInfo,
        disable_stage_packages: bool = False,
        disable_build_packages: bool = False,
        extra_build_packages: List[str] = None
    ):
        self._part_list = part_list
        self._validator = validator
        self._project_info = project_info
        self._disable_stage_packages = disable_stage_packages
        self._disable_build_packages = disable_build_packages
        self._extra_build_packages = extra_build_packages
        self._handler: Dict[str, PartHandler] = {}

    def prologue(self):
        """Prepare the execution environment."""

        if not self._disable_build_packages:
            for part in self._part_list:
                self._create_part_handler(part)

            all_build_packages = set()
            for _, handler in self._handler.items():
                all_build_packages.update(handler.build_packages)

            if self._extra_build_packages:
                all_build_packages.update(self._extra_build_packages)

            packages.Repository.install_build_packages(sorted(all_build_packages))
            # TODO: install build snaps

        callbacks.run_prologue(self._project_info, part_list=self._part_list)

    def epilogue(self):
        """Finish and clean the execution environment."""
        callbacks.run_epilogue(self._project_info, part_list=self._part_list)

    def run_action(self, action: Action, *, part: Part):
        """Execute the given action for a part using the provided step information."""

        logger.debug("execute action %s:%s", part.name, action)

        if action.type == ActionType.SKIP:
            logger.debug("Skip execution of %s (because %s)", action, action.reason)
            return

        if action.step == Step.STAGE:
            check_for_stage_collisions(self._part_list)

        self._create_part_handler(part)

        handler = self._handler[part.name]
        handler.run_action(action)

    def clean(self, *, step: Step, part_names: List[str] = None):
        """Clean the given parts, or all parts if none is specified."""

        if not part_names:
            self._clean_all_parts(step=step)
            return

        selected_parts = part_list_by_name(part_names, self._part_list)

        selected_steps = [step] + step.next_steps()
        selected_steps.reverse()

        for part in selected_parts:
            self._create_part_handler(part)
            handler = self._handler[part.name]

            for step in selected_steps:
                handler.clean_step(step=step)

    def _clean_all_parts(self, *, step: Step):
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(self._project_info.prime_dir)
            if step <= Step.STAGE:
                shutil.rmtree(self._project_info.stage_dir)
            if step <= Step.PULL:
                shutil.rmtree(self._project_info.parts_dir)

    def _create_part_handler(self, part: Part):
        if part.name not in self._handler:
            # create the part handler for a new part
            self._handler[part.name] = PartHandler(
                part,
                plugin_version=self._project_info.plugin_version,
                part_info=PartInfo(self._project_info, part),
                validator=self._validator,
                part_list=self._part_list,
                disable_stage_packages=self._disable_stage_packages,
            )
