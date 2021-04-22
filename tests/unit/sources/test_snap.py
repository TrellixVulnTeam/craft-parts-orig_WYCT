# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2019 Canonical Ltd
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

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from craft_parts.sources import snap, sources

_LOCAL_DIR = Path(__file__).parent


class TestSnap:
    """Snap source pull tests."""

    @pytest.fixture(autouse=True)
    def setup_method_fixture(self, new_dir):
        # pylint: disable=attribute-defined-outside-init
        self._path = new_dir
        self._test_file = _LOCAL_DIR / "data" / "test-snap.snap"
        self._dest_dir = new_dir / "dest_dir"
        self._dest_dir.mkdir()
        # pylint: enable=attribute-defined-outside-init

    def test_pull_snap_file_must_extract(self):
        snap_source = sources.Snap(self._test_file, self._dest_dir)
        snap_source.pull()

        assert Path(self._dest_dir / "meta.basic").is_dir()
        assert Path(self._dest_dir / "meta.basic/snap.yaml").is_file()

    def test_pull_snap_must_not_clean_targets(self, mocker):
        mock_provision = mocker.patch.object(sources.Snap, "provision")
        snap_source = sources.Snap(self._test_file, self._dest_dir)
        snap_source.pull()

        mock_provision.assert_called_once_with(
            self._dest_dir,
            clean_target=False,
            src=os.path.join(self._dest_dir, "test-snap.snap"),
        )

    def test_has_source_handler_entry_on_linux(self):
        if sys.platform == "linux":
            assert sources._source_handler["snap"] is sources.Snap
        else:
            assert "snap" not in sources._source_handler

    def test_pull_failure_bad_unsquash(self, mocker):
        mocker.patch(
            "subprocess.check_output", side_effect=subprocess.CalledProcessError(1, [])
        )
        snap_source = sources.Snap(self._test_file, self._dest_dir)

        with pytest.raises(sources.errors.PullError) as raised:
            snap_source.pull()

        assert re.match(
            r"unsquashfs -force -dest {0}/\w+ {0}/dest_dir/test-snap.snap".format(
                self._path
            ),
            getattr(raised.value, "command"),
        )
        assert getattr(raised.value, "exit_code") == 1


@pytest.mark.usefixtures("new_dir")
class TestGetName:
    """Checks for snap name retrieval from snap.yaml."""

    def test_get_name(self):
        os.mkdir("meta")

        with open(os.path.join("meta", "snap.yaml"), "w") as snap_yaml_file:
            print("name: my-snap", file=snap_yaml_file)
        assert snap._get_snap_name(".") == "my-snap"

    def test_no_name_yaml(self):
        os.mkdir("meta")

        with open(os.path.join("meta", "snap.yaml"), "w") as snap_yaml_file:
            print("summary: no name", file=snap_yaml_file)

        with pytest.raises(sources.errors.InvalidSnapPackage):
            snap._get_snap_name(".")

    def test_no_snap_yaml(self):
        os.mkdir("meta")

        with pytest.raises(sources.errors.InvalidSnapPackage):
            snap._get_snap_name(".")
