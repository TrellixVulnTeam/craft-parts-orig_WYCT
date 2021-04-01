# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2020 Canonical Ltd
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

"""The dump plugin.

This plugin just dumps the content from a specified part source.
"""

from typing import Any, Dict, List, Set, Type

from ..plugin_v2 import PluginV2, PluginV2Properties


class DumpPluginProperties(PluginV2Properties):
    @classmethod
    def unmarshal(cls, data: Dict[str, Any]):
        return cls()


class DumpPlugin(PluginV2):
    """Copy the content from the part source."""

    @classmethod
    def get_properties_class(cls) -> Type[DumpPluginProperties]:
        return DumpPluginProperties

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": ["source"],
        }

    def get_build_snaps(self) -> Set[str]:
        return set()

    def get_build_packages(self) -> Set[str]:
        return set()

    def get_build_environment(self) -> Dict[str, str]:
        return dict()

    def get_build_commands(self) -> List[str]:
        install_dir = self._part_info.part_install_dir
        return [f'cp --archive --link --no-dereference . "{install_dir}"']
