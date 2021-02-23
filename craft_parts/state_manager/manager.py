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

"""The part crafter lifecycle manager."""

import contextlib
import itertools
import logging
from datetime import datetime
from typing import Dict, Final, List, Optional

from craft_parts import errors, parts, sources, steps
from craft_parts.infos import ProjectInfo
from craft_parts.parts import Part
from craft_parts.schemas import Validator
from craft_parts.sources import SourceHandler
from craft_parts.state_manager import states
from craft_parts.steps import Step

from .dirty_report import Dependency, DirtyReport
from .outdated_report import OutdatedReport
from .part_state import PartState
from .states import load_state

logger = logging.getLogger(__name__)


# report types
_DirtyReports = Dict[str, Dict[Step, Optional[DirtyReport]]]
_OutdatedReports = Dict[str, Dict[Step, Optional[OutdatedReport]]]


class _StateWrapper:
    """A wrapper for the in-memory PartState class with extra metadata.

    This is a wrapper class for PartState that stores additional metadata
    such as the file timestamp when loading the persistent state from a
    previous lifecycle run. The update status is used to check if an
    outdated step was scheduled to be updated. This is stored as metadata
    because the outdated step verification checks data on disk which will
    only be updated when the update action runs (later, in the execution
    phase).
    """

    def __init__(
        self,
        state: PartState,
        timestamp: Optional[datetime] = None,
        serial: int = 0,
        updated: bool = False,
    ):
        if (timestamp and serial) or not (timestamp or serial):
            raise errors.InternalError("either timestamp or serial must be provided")

        self._state: Final[PartState] = state
        self._timestamp: Final[Optional[datetime]] = timestamp
        self._serial: Final[int] = serial
        self._updated: Final[bool] = updated

    @property
    def state(self) -> PartState:
        """The wrapped state data."""
        return self._state

    @property
    def timestamp(self) -> Optional[datetime]:
        """The state timestamp metadata set from the state file."""
        return self._timestamp

    @property
    def updated(self) -> bool:
        """Verify whether this state was updated after reported outdated."""
        return self._updated

    def is_newer_than(self, other: "_StateWrapper"):
        """Verify if this state is newer than the specified state.

        :param other: The state to compare this state to.
        """

        # both states have timestamps, check which is newer
        if self.timestamp and other.timestamp:
            return self.timestamp > other.timestamp

        # we have a timestamp and the other doesn't, other is newer
        if self.timestamp and not other.timestamp:
            return False

        # we don't have a timestamp but other does, we're newer
        if not self.timestamp and other.timestamp:
            return True

        # neither state has a timestamp, compare serials
        return self._serial > other._serial


class _EphemeralStates:
    def __init__(self):
        self._state: Dict[str, Dict[Step, _StateWrapper]] = {}
        self._serial_gen = itertools.count(1)

    def new_ephemeral_state(
        self, state: PartState, updated: bool = False
    ) -> _StateWrapper:
        """Create a state wrapper from a pure in-memory state.

        :param state: The part state to store.
        :param updated: Whether this state was updated after an outdated report.
        """

        # We use serials instead of timestamps for in-memory states to avoid
        # doing logic based on timestamp comparisons during the planning phase.
        stw = _StateWrapper(state, serial=next(self._serial_gen), updated=updated)
        return stw

    def set(
        self, *, part_name: str, step: Step, state: Optional[_StateWrapper]
    ) -> None:
        """Set a state for a given part and step."""
        if not state:
            self.remove(part_name=part_name, step=step)
            return

        if part_name not in self._state:
            self._state[part_name] = dict()

        self._state[part_name][step] = state

    def remove(self, *, part_name: str, step: Step) -> None:
        """Remove the state for a given part and step."""
        if part_name in self._state:
            self._state[part_name].pop(step, None)

    def test(self, *, part_name: str, step: Step) -> bool:
        """Verify if there is a state for a given part and step."""
        if part_name not in self._state:
            return False
        return step in self._state[part_name]

    def get(self, *, part_name: str, step: Step) -> Optional[_StateWrapper]:
        """Retrieve the state for a give part and step."""
        if self.test(part_name=part_name, step=step):
            return self._state[part_name][step]
        return None

    def update_timestamp(self, *, part_name: str, step: Step) -> None:
        """Update the state timestamp (actually adds an ephemeral serial)."""
        stw = self.get(part_name=part_name, step=step)
        if stw:
            # rewrap the state with new metadata
            new_stw = self.new_ephemeral_state(stw.state)
            self.set(part_name=part_name, step=step, state=new_stw)

    def set_updated(self, *, part_name: str, step: Step) -> None:
        """Mark this part and step as updated."""
        stw = self.get(part_name=part_name, step=step)
        if stw:
            # rewrap the state with new metadata
            new_stw = self.new_ephemeral_state(stw.state, updated=True)
            self.set(part_name=part_name, step=step, state=new_stw)

    def was_updated(self, *, part_name: str, step: Step) -> bool:
        """Verify whether the part and step was updated."""
        if self.test(part_name=part_name, step=step):
            return self._state[part_name][step].updated
        return False


class StateManager:
    """Keep track of parts execution state."""

    def __init__(
        self, project_info: ProjectInfo, all_parts: List[Part], validator: Validator
    ):
        self._state = _EphemeralStates()
        self._project_info = project_info
        self._all_parts = all_parts
        self._validator = validator
        self._source_handler_cache: Dict[str, Optional[SourceHandler]] = {}

        for part in all_parts:
            # Initialize from persistent state
            for step in list(Step):
                state, timestamp = load_state(part, step)
                if state and timestamp:
                    self._state.set(
                        part_name=part.name,
                        step=step,
                        state=_StateWrapper(state, timestamp=timestamp),
                    )

    def set_state(self, part: Part, step: Step, *, state: PartState) -> None:
        """Set the ephemeral state of the given part and step."""
        stw = self._state.new_ephemeral_state(state)
        self._state.set(part_name=part.name, step=step, state=stw)

    def update_state_timestamp(self, part: Part, step: Step) -> None:
        """Update the given part and step state's timestamp."""
        self._state.update_timestamp(part_name=part.name, step=step)

    def should_step_run(self, part: Part, step: Step) -> bool:
        """Determine if a given step of a given part should run.

        :param Part part: Part in question.
        :param Step step: Step in question.
        :return: Whether or not step should run.
        :rtype: bool

        A given step should run if it:
            1. Hasn't yet run
            2. Is dirty
            3. Is outdated
            4. Either (1), (2), or (3) apply to any earlier steps in the part's
               lifecycle
        """
        if (
            not self.has_step_run(part, step)
            or self.outdated_report(part, step) is not None
            or self.dirty_report(part, step) is not None
        ):
            return True

        previous_steps = step.previous_steps()
        if previous_steps:
            return self.should_step_run(part, previous_steps[-1])

        return False

    def has_step_run(self, part: Part, step: Step) -> bool:
        """Determine if a given step of a given part has already run.

        :param Part part: Part in question.
        :param Step step: Step in question.
        :return: Whether or not the step has run.
        """
        return self._state.test(part_name=part.name, step=step)

    def clean_part(self, part: Part, step: Step) -> None:
        """Remove the state for this and all later steps."""

        for next_step in [step] + step.next_steps():
            self._state.remove(part_name=part.name, step=next_step)

    def dirty_report(self, part: Part, step: Step) -> Optional[DirtyReport]:
        """Obtain the dirty report for a given step of the given part.

        :param Part part: Part in question.
        :param Step step: Step in question.
        :return: Dirty report (could be None)
        """

        # Get the dirty report from the PluginHandler. If it's dirty, we can
        # stop here
        report = self._dirty_report_for_part(part, step)
        if report:
            return report

        prerequisite_step = steps.dependency_prerequisite_step(step)
        if not prerequisite_step:
            return None

        # The dirty report from the PluginHandler only takes into account
        # properties specific to that part. If it's not dirty because of those,
        # we need to expand it here to also take its dependencies (if any) into
        # account
        dependencies = parts.part_dependencies(
            part.name, part_list=self._all_parts, recursive=True
        )

        changed_dependencies: List[Dependency] = []

        stw = self._state.get(part_name=part.name, step=step)
        logger.debug("state for %s:%s: %s", part.name, step, stw)

        # consistency check
        if not stw:
            raise errors.InternalError(
                f"{part.name}:{step} should already have been run"
            )

        for dependency in dependencies:
            # Make sure the prerequisite step of this dependency has not
            # run more recently than (or should run _before_) this step.

            prerequisite_stw = self._state.get(
                part_name=dependency.name, step=prerequisite_step
            )
            if prerequisite_stw:
                dependency_changed = prerequisite_stw.is_newer_than(stw)
            else:
                dependency_changed = True

            if dependency_changed or self.should_step_run(
                dependency, prerequisite_step
            ):
                changed_dependencies.append(
                    Dependency(part_name=dependency.name, step=prerequisite_step)
                )

        if changed_dependencies:
            return DirtyReport(changed_dependencies=changed_dependencies)

        return None

    def outdated_report(self, part: Part, step: Step) -> Optional[OutdatedReport]:
        """Obtain the outdated report for a given step of the given part.

        :param Part part: Part in question.
        :param Step step: Step in question.
        :return: Outdated report (could be None)
        """
        if self._state.was_updated(part_name=part.name, step=step):
            return None

        return self._outdated_report_for_part(part, step)

    def _dirty_report_for_part(self, part: Part, step: Step) -> Optional[DirtyReport]:
        """Return a DirtyReport class describing why the step is dirty.

        A step is considered to be dirty if either YAML properties used by it
        (`stage-packages` are used by the `pull` step, for example), or project
        options used by it (`--target-arch` is used by the `pull` step as well)
        have changed since the step was run. This means the step needs to be
        cleaned and run again. This is in contrast to an "outdated" step, which
        typically doesn't need to be cleaned, just updated with files from an
        earlier step in the lifecycle.

        :param steps.Step step: The step to be checked.
        :returns: DirtyReport if the step is dirty, None otherwise.
        """

        # Retrieve the stored state for this step (assuming it has already run)
        stw = self._state.get(part_name=part.name, step=step)
        if stw:
            state = stw.state
            # state properties contains the old state that this step cares
            # about, and we're comparing it to those same keys in the current
            # state (current_properties). If they've changed, then this step
            # is dirty and needs to run again.
            part_properties = self._validator.expand_part_properties(part.properties)
            properties = state.diff_properties_of_interest(part_properties)

            # state project_options contains the old project options that this
            # step cares about, and we're comparing it to those same options in
            # the current state. If they've changed, then this step is dirty
            # and needs to run again.
            options = state.diff_project_options_of_interest(
                self._project_info.project_options
            )

            if properties or options:
                return DirtyReport(
                    dirty_properties=list(properties),
                    dirty_project_options=list(options),
                )

        return None

    def mark_step_updated(self, part: Part, step: Step):
        """Mark the given part and step as updated."""
        self._state.set_updated(part_name=part.name, step=step)

    def _outdated_report_for_part(
        self, part: Part, step: Step
    ) -> Optional[OutdatedReport]:
        """Return an OutdatedReport class describing why the step is outdated.

        A step is considered to be outdated if an earlier step in the lifecycle
        has been run more recently, or if the source code changed on disk.
        This means the step needs to be updated by taking modified files from
        the previous step. This is in contrast to a "dirty" step, which must
        be cleaned and run again.

        :param steps.Step step: The step to be checked.
        :returns: OutdatedReport if the step is outdated, None otherwise.
        """

        stw = self._state.get(part_name=part.name, step=step)
        if not stw:
            return None

        if step == Step.PULL:
            if part.name in self._source_handler_cache:
                source_handler = self._source_handler_cache[part.name]
            else:
                source_handler = sources.get_source_handler(
                    application_name=self._project_info.application_name,
                    source=part.source,
                    source_dir=part.part_src_dir,
                    properties=self._validator.expand_part_properties(part.properties),
                )
                self._source_handler_cache[part.name] = source_handler

            state_file = states.state_file_path(part, step)

            if source_handler:
                # Not all sources support checking for updates
                with contextlib.suppress(sources.errors.SourceUpdateUnsupported):
                    if source_handler.check(str(state_file)):
                        return OutdatedReport(source_updated=True)

            return None

        for previous_step in reversed(step.previous_steps()):
            # Has a previous step run since this one ran? Then this
            # step needs to be updated.
            previous_stw = self._state.get(part_name=part.name, step=previous_step)

            if previous_stw and previous_stw.is_newer_than(stw):
                return OutdatedReport(previous_step_modified=previous_step)

        return None
