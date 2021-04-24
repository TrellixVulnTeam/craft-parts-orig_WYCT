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

"""The parts lifecycle manager definition and helpers."""

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from craft_parts import errors, executor, packages, parts, plugins, sequencer
from craft_parts.actions import Action
from craft_parts.dirs import ProjectDirs
from craft_parts.infos import ProjectInfo
from craft_parts.parts import Part
from craft_parts.steps import Step

_SCHEMA_DIR = Path(__file__).parent / "data" / "schema"


class ExecutionContext:
    """A context manager to handle lifecycle action executions."""

    def __init__(
        self,
        prologue: Callable[[], None],
        epilogue: Callable[[], None],
        execute: Callable[[Union[Action, List[Action]]], None],
    ):
        self._prologue = prologue
        self._epilogue = epilogue
        self._execute = execute

    def __enter__(self) -> "ExecutionContext":
        self._prologue()
        return self

    def __exit__(self, *exc):
        self._epilogue()

    def execute(self, actions: Union[Action, List[Action]]) -> None:
        """Execute the specified action or list of actions.

        :param actions: An :class:`Action` object or list of :class:`Action`
           objects specifying steps to execute.

        :raises InvalidActionException: If the action parameters are invalid.
        """
        self._execute(actions)


class LifecycleManager:
    """Coordinate the planning and execution of the parts lifecycle.

    The lifecycle manager determines the list of actions that needs be executed in
    order to obtain a tree of installed files from the specification on how to
    process its parts, and provides a mechanism to execute each of these actions.

    :param all_parts: a dictionary containing the parts specification according
        to the :ref:`parts schema<parts-schema>`. The format is compatible with the
        output generated by PyYAML's ``yaml.load``.
    :param application_name: the application using Craft Parts. This string will
        be used when creating persistent data that shouldn't be shared with
        other applications.
    :param build_packages: a list of additional build packages to install.
    :param work_dir: the toplevel directory for the Craft Parts work tree. The
        current directory will be used if none is specified.
    :param arch: The target architecture to build for, if cross-compiling.
    :param parallel_build_count: The maximum number of concurrent jobs to be
        used to build each part of this project.
    :param local_plugins_dir: The directory where local plugins are, if any.
    :param stage_pkg_unpack: Enable unpacking stage packages defined for each
        part into the part's install directory.
    :param build_pkg_install: Enable installing build packages defined for each
        part when running the execution prologue.
    :param custom_args: Any additional arguments that will be passed directly
        to :ref:`callbacks<callbacks>`.
    """

    def __init__(
        self,
        all_parts: Dict[str, Any],
        *,
        application_name: str,
        build_packages: List[str] = None,
        work_dir: str = ".",
        arch: str = "",
        parallel_build_count: int = 1,
        local_plugins_dir: str = "",
        extra_build_packages: List[str] = None,
        base_packages: List[str] = None,
        base_dir: Union[str, Path] = None,
        **custom_args,  # custom passthrough args
    ):
        if base_packages and not base_dir:
            raise ValueError("base_dir is mandatory if base_packages are specified")

        if "parts" not in all_parts:
            raise errors.SchemaValidationError("parts definition is missing")

        project_dirs = ProjectDirs(work_dir=work_dir)

        project_info = ProjectInfo(
            application_name=application_name,
            arch=arch,
            parallel_build_count=parallel_build_count,
            project_dirs=project_dirs,
            local_plugins_dir=local_plugins_dir,
            **custom_args,
        )

        parts_data = deepcopy(all_parts.get("parts"))
        if not isinstance(parts_data, dict):
            raise errors.SchemaValidationError("parts definition is malformed")

        part_list = []
        for name, spec in parts_data.items():
            part_list.append(_build_part(name, spec, project_dirs))

        self._part_list = part_list
        self._application_name = application_name
        self._target_arch = project_info.target_arch
        self._base_packages = base_packages
        self._build_packages = build_packages
        self._sequencer = sequencer.Sequencer(
            part_list=self._part_list,
            project_info=project_info,
        )
        self._executor = executor.Executor(
            part_list=self._part_list,
            project_info=project_info,
            extra_build_packages=extra_build_packages,
            base_packages=base_packages,
            base_dir=base_dir,
        )
        self._project_info = project_info

        # TODO: validate/transform application name, should be usable in file names
        #       consider using python-slugify here

    @property
    def project_info(self) -> ProjectInfo:
        """Obtain information about this project."""
        return self._project_info

    def clean(self, step: Optional[Step] = None, part_names: List[str] = None) -> None:
        """Clean the specified parts.""

        :para step: The step to clean.
        :param part_names: The list of part names to clean. If not specified,
            all parts will be cleaned.
        """

        if not step:
            step = Step.PULL

        self._executor.clean(initial_step=step, part_names=part_names)

    def update(self, update_system_package_list=False) -> None:
        """Refresh the available packages list.

        The list of available packages should be updated before planning the
        sequence of actions to take. To ensure consistency between the scenarios,
        it shouldn't be updated between planning and execution.

        :param update_system_package_list: Also refresh the list of available
            build packages to install on the system.
        """

        self._executor.refresh_base_packages_list()

        packages.Repository.update_package_list(
            application_name=self._application_name, target_arch=self._target_arch
        )

        if update_system_package_list:
            packages.Repository.refresh_build_packages()

    def plan(self, target_step: Step, part_names: Sequence[str] = None) -> List[Action]:
        """Obtain the list of actions to be executed given the target step and parts.

        :param target_step: The final step we want to reach.
        :param part_names: The list of parts to process. If not specified, all
            parts will be processed.
        :param update: refresh the list of available packages.

        :return: The list of :class:`Action` objects that should be executed in
            order to reach the target step for the specified parts.
        """

        if self._base_packages:
            # check if base packages changed
            base_deps = self._executor.resolve_base_packages_dependencies(
                self._base_packages
            )
            layer_state = self._executor.load_base_packages_state()

            if not layer_state or set(base_deps) != set(layer_state.base_packages):
                self.clean(Step.PULL)
                self.reload_state()
                self._executor.clean_base_packages_layers()

        act = self._sequencer.plan(target_step, part_names)
        return act

    def reload_state(self) -> None:
        """Reload the ephemeral state from disk."""
        self._sequencer.reload_state()

    def action_executor(self) -> ExecutionContext:
        """Return a context manager for action execution."""
        return ExecutionContext(
            self._execution_prologue, self._execution_epilogue, self._execute
        )

    def _execute(self, actions: Union[Action, List[Action]]) -> None:
        """Execute the specified action or list of actions.

        :param actions: An :class:`Action` object or list of :class:`Action`
           objects specifying steps to execute.

        :raises InvalidActionException: If the action parameters are invalid.
        """

        if isinstance(actions, Action):
            actions = [actions]

        for act in actions:
            part = parts.part_by_name(act.part_name, self._part_list)
            self._executor.run_action(act, part=part)

    def _execution_prologue(self) -> None:
        """Prepare the execution environment.

        This method should be called before executing lifecycle actions.
        Alternatively, calls to :method:`execute` can be placed inside a
        :class:`ExecutionContext` context so that :method:`execution_start`
        and :method:`execution_end` are called automatically.
        """
        self._executor.prologue()

    def _execution_epilogue(self) -> None:
        """Finish and clean the execution environment.

        This method should be called after executing lifecycle actions.
        Alternatively, calls to :method:`execute` can be placed inside a
        :class:`ExecutionContext` context so that :method:`execution_start`
        and :method:`execution_end` are called automatically.
        """
        self._executor.epilogue()


def _build_part(name: str, spec: Dict[str, Any], project_dirs: ProjectDirs) -> Part:
    if not isinstance(spec, dict):
        raise errors.SchemaValidationError(f"part {name!r} definition is malformed")

    plugin_name = spec.get("plugin")
    if not plugin_name:
        plugin_name = name

    plugin_class = plugins.get_plugin_class(plugin_name)
    properties = plugin_class.properties_class.unmarshal(spec)
    _strip_plugin_properties(spec, plugin_name=plugin_name)

    # initialize part and unmarshal part specs
    part = Part(name, spec, project_dirs=project_dirs, plugin_properties=properties)

    # all entries should have been handled
    # if any(spec):
    #     remainder = spec.keys()
    #     raise errors.SchemaValidationError(
    #         "additional properties are not allowed ({} {} unexpected)".format(
    #             formatting_utils.humanize_list(remainder, "and"),
    #             formatting_utils.pluralize(remainder, "is", "are"),
    #         )
    #     )

    return part


def _strip_plugin_properties(data: Dict[str, Any], *, plugin_name: str) -> None:
    prefix = f"{plugin_name}-"
    for key in data:
        if key.startswith(prefix):
            del data[key]
