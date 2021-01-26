# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2016-2021 Canonical Ltd
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

"""Handle the execution of built-in or user specified step commands."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Optional

from craft_parts import errors, filesets, plugins
from craft_parts.filesets import Fileset
from craft_parts.parts import Part
from craft_parts.plugins import Plugin
from craft_parts.sources import SourceHandler
from craft_parts.step_info import StepInfo
from craft_parts.steps import Step
from craft_parts.utils import file_utils

from . import environment


class Runner:
    """Executes built-in or user-specified step commands.

    Default commands to be executed in a step can be overridden by the user
    through scriptlets specified in the parts definition. Furthermore,
    scriptlets can call the default built-in action for a step by invoking
    a control utility. This class offers methods to run scriptlets or built-in
    commands and a FIFO-based mechanism and API to be used by an external
    utility to communicate with the running instance.
    """

    def __init__(
        self,
        part: Part,
        step: Step,
        *,
        step_info: StepInfo,
        plugin: Plugin,
        source_handler: Optional[SourceHandler],
    ):
        self._part = part
        self._step = step
        self._step_info = step_info
        self._plugin = plugin
        self._source_handler = source_handler
        self._env = environment.generate_part_environment(
            part=part, step=step, plugin=plugin, step_info=step_info
        )

    def run_builtin(self):
        """Run the built-in commands for the current step."""

        if self._step == Step.PULL:
            self._builtin_pull()
        elif self._step == Step.BUILD:
            self._builtin_build()
        elif self._step == Step.STAGE:
            self._builtin_stage()
        elif self._step == Step.PRIME:
            self._builtin_prime()

    def _builtin_pull(self):
        if self._source_handler:
            self._source_handler.pull()

    def _builtin_build(self):
        if not isinstance(self._plugin, plugins.PluginV2):
            raise errors.InternalError("Plugin version not supported.")

        _do_v2_build(part=self._part, plugin=self._plugin, env=self._env)

    def _builtin_stage(self):
        stage_fileset = Fileset(self._part.stage_fileset)
        srcdir = str(self._part.part_install_dir)
        files, dirs = filesets.migratable_filesets(stage_fileset, srcdir)
        _migrate_files(
            files=files,
            dirs=dirs,
            srcdir=self._part.part_install_dir,
            destdir=self._part.stage_dir,
        )

    def _builtin_prime(self):
        prime_fileset = Fileset(self._part.prime_fileset)

        # If we're priming and we don't have an explicit set of files to prime
        # include the files from the stage step
        if prime_fileset.entries == ["*"] or len(prime_fileset.includes) == 0:
            stage_fileset = Fileset(self._part.stage_fileset)
            prime_fileset.combine(stage_fileset)

        srcdir = str(self._part.part_install_dir)
        files, dirs = filesets.migratable_filesets(prime_fileset, srcdir)
        _migrate_files(
            files=files,
            dirs=dirs,
            srcdir=self._part.stage_dir,
            destdir=self._part.prime_dir,
        )
        # TODO: handle elf dependencies

    def run_scriptlet(
        self, scriptlet: str, *, scriptlet_name: str, workdir: Path
    ) -> None:
        """Execute a scriptlet.

        :param scriptlet: the scriptlet to run.
        :param workdir: the directory where the script will be executed.
        """

        with tempfile.TemporaryDirectory() as tempdir:
            call_fifo = file_utils.NonBlockingRWFifo(
                os.path.join(tempdir, "function_call")
            )
            feedback_fifo = file_utils.NonBlockingRWFifo(
                os.path.join(tempdir, "call_feedback")
            )

            env = environment.generate_part_environment(
                part=self._part,
                step=self._step,
                plugin=self._plugin,
                step_info=self._step_info,
            )

            # snapcraftctl only works consistently if it's using the exact same
            # interpreter as that used by snapcraft itself, thus the definition
            # of SNAPCRAFT_INTERPRETER.
            script = textwrap.dedent(
                """\
                set -e
                export CRAFT_PARTS_CALL_FIFO={call_fifo}
                export CRAFT_PARTS_FEEDBACK_FIFO={feedback_fifo}
                export CRAFT_PARTS_INTERPRETER={interpreter}

                {env}

                {scriptlet}"""
            ).format(
                interpreter=sys.executable,
                call_fifo=call_fifo.path,
                feedback_fifo=feedback_fifo.path,
                scriptlet=scriptlet,
                env=env,
            )

            with tempfile.TemporaryFile(mode="w+") as script_file:
                print(script, file=script_file)
                script_file.flush()
                script_file.seek(0)
                process = subprocess.Popen(["/bin/sh"], stdin=script_file, cwd=workdir)

            status = None
            try:
                while status is None:
                    function_call = call_fifo.read()
                    if function_call:
                        # Handle the function and let caller know that function
                        # call has been handled (must contain at least a
                        # newline, anything beyond is considered an error by
                        # snapcraftctl)
                        self._handle_control_api(scriptlet_name, function_call.strip())
                        feedback_fifo.write("\n")

                    status = process.poll()

                    # Don't loop TOO busily
                    time.sleep(0.1)
            except Exception as error:
                feedback_fifo.write(f"{error!s}\n")
                raise error
            finally:
                call_fifo.close()
                feedback_fifo.close()

            if process.returncode != 0:
                raise errors.ScriptletRunError(
                    scriptlet_name=scriptlet_name, code=status
                )

    def _handle_control_api(self, scriptlet_name, function_call) -> None:
        try:
            function_json = json.loads(function_call)
        except json.decoder.JSONDecodeError as err:
            raise errors.InternalError(
                "{!r} scriptlet called a function with invalid json: "
                "{}".format(scriptlet_name, function_call)
            ) from err

        for attr in ["function", "args"]:
            if attr not in function_json:
                raise errors.InternalError(
                    f"{scriptlet_name} control call missing attribute {attr!r}"
                )

        function_name = function_json["function"]
        # function_args = function_json["args"]

        if function_name == "pull":
            self._builtin_pull()
        elif function_name == "build":
            self._builtin_build()
        elif function_name == "stage":
            self._builtin_stage()
        elif function_name == "prime":
            self._builtin_prime()
        else:
            raise errors.InvalidControlAPICall(
                self._part.name, f"invalid function {function_name!r}"
            )


def _do_v2_build(*, part: Part, plugin: Plugin, env: str) -> None:
    # Save script to execute.
    build_script_path = part.part_run_dir / "build.sh"

    # Plugin commands.
    plugin_build_commands = plugin.get_build_commands()

    with build_script_path.open("w") as run_file:
        print(env, file=run_file)
        print("set -x", file=run_file)

        for build_command in plugin_build_commands:
            print(build_command, file=run_file)

        run_file.flush()

    build_script_path.chmod(0o755)

    try:
        subprocess.run([build_script_path], check=True, cwd=part.part_build_work_dir)
    except subprocess.CalledProcessError as process_error:
        raise errors.PluginBuildError(part_name=part.name) from process_error


def _migrate_files(
    *,
    files,
    dirs,
    srcdir,
    destdir,
    missing_ok=False,
    follow_symlinks=False,
    fixup_func=lambda *args: None,
):
    for dirname in sorted(dirs):
        src = os.path.join(srcdir, dirname)
        dst = os.path.join(destdir, dirname)

        file_utils.create_similar_directory(src, dst)

    for filename in sorted(files):
        src = os.path.join(srcdir, filename)
        dst = os.path.join(destdir, filename)

        if missing_ok and not os.path.exists(src):
            continue

        # If the file is already here and it's a symlink, leave it alone.
        if os.path.islink(dst):
            continue

        # Otherwise, remove and re-link it.
        if os.path.exists(dst):
            os.remove(dst)

        file_utils.link_or_copy(src, dst, follow_symlinks=follow_symlinks)

        fixup_func(dst)
