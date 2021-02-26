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

from typing import List

import pytest

from craft_parts import callbacks, errors
from craft_parts.infos import PartInfo, ProjectInfo, StepInfo
from craft_parts.parts import Part
from craft_parts.steps import Step


def _callback_1(info: StepInfo) -> bool:
    greet = getattr(info, "greet")
    print(f"{greet} callback 1")
    return True


def _callback_2(info: StepInfo) -> bool:
    greet = getattr(info, "greet")
    print(f"{greet} callback 2")
    return False


def _callback_3(info: ProjectInfo, part_list: List[Part]) -> None:
    greet = getattr(info, "greet")
    names = " ".join([x.name for x in part_list])
    print(f"{greet} callback 3 ({names})")


def _callback_4(info: ProjectInfo, part_list: List[Part]) -> None:
    greet = getattr(info, "greet")
    names = " ".join([x.name for x in part_list])
    print(f"{greet} callback 4 ({names})")


class TestCallbackRegistration:
    """Test different scenarios of callback function registration."""

    def setup_method(self):
        callbacks.clear()

    def test_register_pre_step(self):
        callbacks.register_pre_step(_callback_1)

        # A callback function shouldn't be registered again
        with pytest.raises(errors.CallbackRegistration) as raised:
            callbacks.register_pre_step(_callback_1)
        assert (
            str(raised.value) == "Callback registration error: the callback "
            "function '_callback_1' is already registered."
        )

        # But we can register a different one
        callbacks.register_pre_step(_callback_2)

    def test_register_post_step(self):
        callbacks.register_post_step(_callback_1)

        # A callback function shouldn't be registered again
        with pytest.raises(errors.CallbackRegistration) as raised:
            callbacks.register_post_step(_callback_1)
        assert (
            str(raised.value) == "Callback registration error: the callback "
            "function '_callback_1' is already registered."
        )

        # But we can register a different one
        callbacks.register_post_step(_callback_2)

    def test_register_prologue(self):
        callbacks.register_prologue(_callback_3)

        # A callback function shouldn't be registered again
        with pytest.raises(errors.CallbackRegistration) as raised:
            callbacks.register_prologue(_callback_3)
        assert (
            str(raised.value) == "Callback registration error: the callback "
            "function '_callback_3' is already registered."
        )

        # But we can register a different one
        callbacks.register_prologue(_callback_4)

    def test_register_epilogue(self):
        callbacks.register_epilogue(_callback_3)

        # A callback function shouldn't be registered again
        with pytest.raises(errors.CallbackRegistration) as raised:
            callbacks.register_epilogue(_callback_3)
        assert (
            str(raised.value) == "Callback registration error: the callback "
            "function '_callback_3' is already registered."
        )

        # But we can register a different one
        callbacks.register_epilogue(_callback_4)

    def test_register_both_pre_and_post(self):
        callbacks.register_pre_step(_callback_1)
        callbacks.register_post_step(_callback_1)

    def test_register_both_prologue_and_epilogue(self):
        callbacks.register_prologue(_callback_3)
        callbacks.register_epilogue(_callback_3)

    def test_clear(self):
        callbacks.register_pre_step(_callback_1)
        callbacks.register_post_step(_callback_1)
        callbacks.register_prologue(_callback_3)
        callbacks.register_epilogue(_callback_3)
        callbacks.clear()
        callbacks.register_pre_step(_callback_1)
        callbacks.register_post_step(_callback_1)
        callbacks.register_prologue(_callback_3)
        callbacks.register_epilogue(_callback_3)

    def test_register_steps(self):
        callbacks.register_pre_step(_callback_1, step_list=[Step.PULL, Step.BUILD])

        # A callback function shouldn't be registered again, even for a different step
        with pytest.raises(errors.CallbackRegistration) as raised:
            callbacks.register_pre_step(_callback_1, step_list=[Step.PRIME])
        assert (
            str(raised.value) == "Callback registration error: the callback "
            "function '_callback_1' is already registered."
        )


class TestCallbackExecution:
    """Test different scenarios of callback function execution."""

    # pylint: disable=attribute-defined-outside-init
    def setup_method(self):
        part = Part("foo", {})
        self._project_info = ProjectInfo(
            application_name="test",
            target_arch="x86_64",
            parallel_build_count=4,
            local_plugins_dir=None,
            greet="hello",
        )
        self._part_info = PartInfo(project_info=self._project_info, part=part)
        self._step_info = StepInfo(part_info=self._part_info, step=Step.BUILD)
        callbacks.clear()

    def test_run_pre_step(self, capfd):
        callbacks.register_pre_step(_callback_1)
        callbacks.register_pre_step(_callback_2)
        callbacks.run_pre_step(self._step_info)
        out, err = capfd.readouterr()
        assert not err
        assert out == "hello callback 1\nhello callback 2\n"

    def test_run_post_step(self, capfd):
        callbacks.register_post_step(_callback_1)
        callbacks.register_post_step(_callback_2)
        callbacks.run_post_step(self._step_info)
        out, err = capfd.readouterr()
        assert not err
        assert out == "hello callback 1\nhello callback 2\n"

    def test_run_prologue(self, capfd):
        part1 = Part("p1", {})
        part2 = Part("p2", {})
        callbacks.register_prologue(_callback_3)
        callbacks.register_prologue(_callback_4)
        callbacks.run_prologue(self._project_info, part_list=[part1, part2])
        out, err = capfd.readouterr()
        assert not err
        assert out == "hello callback 3 (p1 p2)\nhello callback 4 (p1 p2)\n"

    def test_run_epilogue(self, capfd):
        part1 = Part("p1", {})
        part2 = Part("p2", {})
        callbacks.register_epilogue(_callback_3)
        callbacks.register_epilogue(_callback_4)
        callbacks.run_epilogue(self._project_info, part_list=[part1, part2])
        out, err = capfd.readouterr()
        assert not err
        assert out == "hello callback 3 (p1 p2)\nhello callback 4 (p1 p2)\n"
