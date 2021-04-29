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

"""Determine the sequence of lifecycle actions to be executed."""

import logging
from pathlib import Path
from typing import List, Optional, Sequence

from craft_parts import common, errors, packages, parts, plugins, steps
from craft_parts.actions import Action, ActionType
from craft_parts.infos import PartInfo, ProjectInfo
from craft_parts.parts import Part, part_list_by_name, sort_parts
from craft_parts.state_manager import StateManager, states
from craft_parts.steps import Step

logger = logging.getLogger(__name__)


class Sequencer:
    """Obtain a list of actions from the parts specification."""

    def __init__(self, *, part_list: List[Part], project_info: ProjectInfo):
        self._part_list = sort_parts(part_list)
        self._project_info = project_info
        self._sm = StateManager(project_info, part_list)
        self._actions: List[Action] = []

    def plan(self, target_step: Step, part_names: Sequence[str] = None) -> List[Action]:
        """Determine the list of steps to execute for each part."""

        self._actions = []
        self._add_all_actions(target_step, part_names)
        return self._actions

    def reload_state(self) -> None:
        """Reload the ephemeral state from disk."""
        self._sm = StateManager(self._project_info, self._part_list)

    def resolve_package_dependencies(self, package_names: List[str]) -> List[str]:
        """Expand the list of provided packages to include dependencies and versions."""

        package_list = packages.Repository.fetch_stage_packages(
            application_name=self._project_info.application_name,
            package_names=package_names,
            target_arch=self._project_info.target_arch,
            base=self._project_info.base,  # os_utils.get_build_base(),
            stage_packages_path=Path(""),  # not used
            list_only=True,
        )

        return package_list

    def _add_all_actions(
        self,
        target_step: Step,
        part_names: Sequence[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        selected_parts = part_list_by_name(part_names, self._part_list)

        for current_step in target_step.previous_steps() + [target_step]:
            # We check for stage collisions when executing the step actions

            for part in selected_parts:
                logger.debug("process %s:%s", part.name, current_step)
                self._add_step_actions(
                    current_step=current_step,
                    target_step=target_step,
                    part=part,
                    part_names=part_names,
                    reason=reason,
                )

    def _add_step_actions(
        self,
        *,
        current_step: Step,
        target_step: Step,
        part: Part,
        part_names: Optional[Sequence[str]],
        reason: Optional[str] = None,
    ) -> None:
        """Verify if this step should be executed."""

        # check if step already ran, if not then run it
        if not self._sm.has_step_run(part, current_step):
            self._run_step(part, current_step, reason=reason)
            return

        # If the step has already run:
        #
        # 1. If the step is the exact step that was requested, and the part was
        #    explicitly listed, run it again.

        if part_names and current_step == target_step and part.name in part_names:
            if not reason:
                reason = "requested step"
            self._rerun_step(part, current_step, reason=reason)
            return

        # 2. If the step is dirty, run it again. A step is considered dirty if
        #    properties used by the step have changed, project options have changed,
        #    or dependencies have been re-staged.

        dirty_report = self._sm.dirty_report(part, current_step)
        if dirty_report:
            logger.debug(
                "%s:%s is dirty: %s", part.name, current_step, dirty_report.summary()
            )
            self._rerun_step(part, current_step, reason=dirty_report.summary())
            return

        # 3. If the step is outdated, run it again (without cleaning if possible).
        #    A step is considered outdated if an earlier step in the lifecycle
        #    has been re-executed.

        outdated_report = self._sm.outdated_report(part, current_step)
        if outdated_report:
            logger.debug("%s:%s is outdated", part.name, current_step)

            if current_step in (Step.PULL, Step.BUILD):
                self._update_step(part, current_step, reason=outdated_report.summary())
            else:
                self._rerun_step(part, current_step, reason=outdated_report.summary())

            self._sm.mark_step_updated(part, current_step)
            return

        # 4. Otherwise just skip it
        self._add_action(
            part, current_step, action_type=ActionType.SKIP, reason="already ran"
        )

    def _prepare_step(self, part: Part, step: Step) -> None:
        all_deps = parts.part_dependencies(part.name, part_list=self._part_list)
        prerequisite_step = steps.dependency_prerequisite_step(step)

        # With v2 plugins we don't need to stage dependencies before PULL
        if not prerequisite_step:
            return

        deps = {p for p in all_deps if self._sm.should_step_run(p, prerequisite_step)}
        for dep in deps:
            self._add_all_actions(
                target_step=prerequisite_step,
                part_names=[dep.name],
                reason=f"required to {_step_verb(step)} {part.name!r}",
            )

    def _run_step(
        self,
        part: Part,
        step: Step,
        *,
        reason: Optional[str] = None,
        rerun: bool = False,
    ) -> None:
        self._prepare_step(part, step)

        if rerun:
            self._add_action(part, step, action_type=ActionType.RERUN, reason=reason)
        else:
            self._add_action(part, step, reason=reason)

        state: states.PartState
        part_properties = part.spec.marshal()

        if step == Step.PULL:
            state = states.PullState(
                part_properties=part_properties,
                project_options=self._project_info.project_options,
            )

        elif step == Step.BUILD:
            part_info = PartInfo(self._project_info, part)

            plugin = plugins.get_plugin(
                part=part,
                part_info=part_info,
                properties=part.plugin_properties,
            )

            build_packages = common.get_build_packages(part=part, plugin=plugin)
            build_snaps = common.get_build_snaps(part=part, plugin=plugin)

            assets = {
                "build-packages": build_packages,
                "build-snaps": build_snaps,
            }
            assets.update(common.get_machine_manifest())

            state = states.BuildState(
                part_properties=part_properties,
                project_options=self._project_info.project_options,
                assets=assets,
            )

        elif step == Step.STAGE:
            state = states.StageState(
                part_properties=part_properties,
                project_options=self._project_info.project_options,
                files=set(),
                directories=set(),
            )

        elif step == Step.PRIME:
            state = states.PrimeState(
                part_properties=part_properties,
                project_options=self._project_info.project_options,
                files=set(),
                directories=set(),
            )

        else:
            raise errors.InternalError("invalid step {step!r}")

        self._sm.set_state(part, step, state=state)

    def _rerun_step(
        self, part: Part, step: Step, *, reason: Optional[str] = None
    ) -> None:
        logger.debug("rerun step %s:%s", part.name, step)

        # clean the step and later steps for this part, then run it again
        self._sm.clean_part(part, step)
        self._run_step(part, step, reason=reason, rerun=True)

    def _update_step(self, part: Part, step: Step, *, reason: Optional[str] = None):
        logger.debug("update step %s:%s", part.name, step)
        self._add_action(part, step, action_type=ActionType.UPDATE, reason=reason)
        self._sm.update_state_timestamp(part, step)

    def _add_action(
        self,
        part: Part,
        step: Step,
        *,
        action_type: ActionType = ActionType.RUN,
        reason: Optional[str] = None,
    ) -> None:
        logger.debug("add action %s:%s(%s)", part.name, step, action_type)
        self._actions.append(
            Action(part.name, step, action_type=action_type, reason=reason)
        )


def _step_verb(step: Step) -> str:
    verb = {
        Step.PULL: "pull",
        Step.BUILD: "build",
        Step.STAGE: "stage",
        Step.PRIME: "prime",
    }
    return verb[step]
