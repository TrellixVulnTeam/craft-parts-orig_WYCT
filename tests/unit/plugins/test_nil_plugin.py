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

from craft_parts.infos import PartInfo, ProjectInfo
from craft_parts.parts import Part
from craft_parts.plugins.nil_plugin import NilPlugin

# pylint: disable=attribute-defined-outside-init


class TestPluginNil:
    """Nil plugin tests."""

    def setup_method(self):
        properties = NilPlugin.properties_class.unmarshal({})
        part = Part("foo", {})

        project_info = ProjectInfo()
        part_info = PartInfo(project_info=project_info, part=part)

        self._plugin = NilPlugin(options=properties, part_info=part_info)

    def test_schema(self):
        schema = NilPlugin.get_schema()
        assert schema["$schema"] == "http://json-schema.org/draft-04/schema#"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"] == {}

    def test_get_build_packages(self):
        assert self._plugin.get_build_packages() == set()
        assert self._plugin.get_build_snaps() == set()

    def test_get_build_environment(self):
        assert self._plugin.get_build_environment() == dict()

    def test_get_build_commands(self):
        assert self._plugin.get_build_commands() == list()
