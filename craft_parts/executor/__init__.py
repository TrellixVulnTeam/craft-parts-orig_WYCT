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

import logging
from typing import Dict, List

from craft_parts.actions import Action, ActionType
from craft_parts.parts import Part
from craft_parts.schemas import Validator
from craft_parts.step_info import StepInfo

from .part_handler import PartHandler

logger = logging.getLogger(__name__)


class Executor:
    """Execute lifecycle actions."""

    def __init__(
        self, *, part_list: List[Part], plugin_version: str, validator: Validator
    ):
        self._part_list = part_list
        self._plugin_version = plugin_version
        self._validator = validator
        self._handler: Dict[str, PartHandler] = {}

    def run_action(self, action: Action, *, part: Part, step_info: StepInfo):
        """Execute the given action for a part using the provided step information."""

        logger.debug("execute action %s:%s", part.name, action)

        if action.type == ActionType.SKIP:
            logger.debug("Skip execution of %s (because %s)", action, action.reason)
            return

        if part.name not in self._handler:
            # create the part handler for a new part
            self._handler[part.name] = PartHandler(
                part,
                plugin_version=self._plugin_version,
                step_info=step_info.for_part(part),
                validator=self._validator,
            )

        handler = self._handler[part.name]
        handler.run_action(action)

    def clean(self, part_names: List[str] = None):
        """Clean the given parts, or all parts if none is specified."""

        # TODO: implement part cleaning
