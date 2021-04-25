# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright 2017-2021 Canonical Ltd.
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

"""Definition and helpers for the repository base class."""

import contextlib
import fileinput
import glob
import itertools
import logging
import os
import re
import shutil
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Pattern, Union

if TYPE_CHECKING:
    from ._base import RepositoryType


logger = logging.getLogger(__name__)


def normalize(unpackdir: str, repository: "RepositoryType") -> None:
    """Normalize artifacts in unpackdir.

    Repo specific packages are generally created to live in a specific
    distro. What normalize does is scan through the unpacked artifacts
    and slightly modifies them to work better with snapcraft projects
    when building and to also work within a snap's environment.

    :param str unpackdir: directory where files where unpacked.
    """
    _remove_useless_files(unpackdir)
    _fix_artifacts(unpackdir, repository)
    _fix_xml_tools(unpackdir)
    _fix_shebangs(unpackdir)


def _remove_useless_files(unpackdir: str) -> None:
    """Remove files that aren't useful or will clash with other parts."""
    sitecustomize_files = glob.glob(
        os.path.join(unpackdir, "usr", "lib", "python*", "sitecustomize.py")
    )
    for sitecustomize_file in sitecustomize_files:
        os.remove(sitecustomize_file)


def _fix_artifacts(unpackdir: str, repository: "RepositoryType") -> None:
    """Perform various modifications to unpacked artifacts.

    Sometimes distro packages will contain absolute symlinks (e.g. if the
    relative path would go all the way to root, they just do absolute). We
    can't have that, so instead clean those absolute symlinks.

    Some unpacked items will also contain suid binaries which we do not
    want in the resulting snap.
    """
    for root, dirs, files in os.walk(unpackdir):
        # Symlinks to directories will be in dirs, while symlinks to
        # non-directories will be in files.
        for entry in itertools.chain(files, dirs):
            path = os.path.join(root, entry)
            if os.path.islink(path) and os.path.isabs(os.readlink(path)):
                _fix_symlink(path, unpackdir, root, repository)
            elif os.path.exists(path):
                _fix_filemode(path)

            if path.endswith(".pc") and not os.path.islink(path):
                fix_pkg_config(unpackdir, path)


def _fix_xml_tools(unpackdir: str) -> None:
    xml2_config_path = os.path.join(unpackdir, "usr", "bin", "xml2-config")
    with contextlib.suppress(FileNotFoundError):
        _search_and_replace_contents(
            xml2_config_path,
            re.compile(r"prefix=/usr"),
            "prefix={}/usr".format(unpackdir),
        )

    xslt_config_path = os.path.join(unpackdir, "usr", "bin", "xslt-config")
    with contextlib.suppress(FileNotFoundError):
        _search_and_replace_contents(
            xslt_config_path,
            re.compile(r"prefix=/usr"),
            "prefix={}/usr".format(unpackdir),
        )


def _fix_symlink(
    path: str, unpackdir: str, root: str, repository: "RepositoryType"
) -> None:
    host_target = os.readlink(path)
    if host_target in repository.get_package_libraries("libc6"):
        logger.debug("Not fixing symlink %s: it's pointing to libc", host_target)
        return

    target = os.path.join(unpackdir, os.readlink(path)[1:])
    if not os.path.exists(target) and not _try_copy_local(path, target):
        return
    os.remove(path)
    os.symlink(os.path.relpath(target, root), path)


def _fix_shebangs(unpackdir: str) -> None:
    """Change hard-coded shebangs in unpacked files to use env."""
    _rewrite_python_shebangs(unpackdir)


def _try_copy_local(path: str, target: str) -> bool:
    real_path = os.path.realpath(path)
    if os.path.exists(real_path):
        logger.warning("Copying needed target link from the system: %s", real_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.readlink(path), target)
        return True

    logger.warning("%s will be a dangling symlink", path)
    return False


def fix_pkg_config(
    root: Union[str, Path],
    pkg_config_file: Union[str, Path],
    prefix_trim: Optional[Union[str, Path]] = None,
) -> None:
    """Open a pkg_config_file and prefixes the prefix with root."""
    pattern_trim = None
    if prefix_trim:
        pattern_trim = re.compile("^prefix={}(?P<prefix>.*)".format(prefix_trim))
    pattern = re.compile("^prefix=(?P<prefix>.*)")

    with fileinput.input(pkg_config_file, inplace=True) as input_file:
        match_trim = None
        for line in input_file:
            match = pattern.search(str(line))
            if prefix_trim is not None and pattern_trim is not None:
                match_trim = pattern_trim.search(str(line))
            if prefix_trim is not None and match_trim is not None:
                print("prefix={}{}".format(root, match_trim.group("prefix")))
            elif match:
                print("prefix={}{}".format(root, match.group("prefix")))
            else:
                print(line, end="")


def _fix_filemode(path: str) -> None:
    mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    if mode & 0o4000 or mode & 0o2000:
        logger.warning("Removing suid/guid from %s", path)
        os.chmod(path, mode & 0o1777)


def _rewrite_python_shebangs(root_dir):
    """Recursively change #!/usr/bin/pythonX shebangs to #!/usr/bin/env pythonX.

    :param str root_dir: Directory that will be crawled for shebangs.
    """
    file_pattern = re.compile(r"")
    argless_shebang_pattern = re.compile(r"\A#!.*(python\S*)$", re.MULTILINE)
    shebang_pattern_with_args = re.compile(
        r"\A#!.*(python\S*)[ \t\f\v]+(\S+)$", re.MULTILINE
    )

    _replace_in_file(
        root_dir, file_pattern, argless_shebang_pattern, r"#!/usr/bin/env \1"
    )

    # The above rewrite will barf if the shebang includes any args to python.
    # For example, if the shebang was `#!/usr/bin/python3 -Es`, just replacing
    # that with `#!/usr/bin/env python3 -Es` isn't going to work as `env`
    # doesn't support arguments like that.
    #
    # The solution is to replace the shebang with one pointing to /bin/sh, and
    # then exec the original shebang with included arguments. This requires
    # some quoting hacks to ensure the file can be interpreted by both sh as
    # well as python, but it's better than shipping our own `env`.
    _replace_in_file(
        root_dir,
        file_pattern,
        shebang_pattern_with_args,
        r"""#!/bin/sh\n''''exec \1 \2 -- "$0" "$@" # '''""",
    )


def _replace_in_file(
    directory: str, file_pattern: Pattern, search_pattern: Pattern, replacement: str
) -> None:
    """Search and replaces patterns that match a file pattern.

    :param directory: The directory to look for files.
    :param file_pattern: The file pattern to match inside directory.
    :param search_pattern: A re.compile'd pattern to search for within matching files.
    :param replacement: The string to replace the matching search_pattern with.
    """
    for root, _, files in os.walk(directory):
        for file_name in files:
            if file_pattern.match(file_name):
                file_path = os.path.join(root, file_name)
                # Don't bother trying to rewrite a symlink. It's either invalid
                # or the linked file will be rewritten on its own.
                if not os.path.islink(file_path):
                    _search_and_replace_contents(file_path, search_pattern, replacement)


def _search_and_replace_contents(
    file_path: str, search_pattern: Pattern, replacement: str
) -> None:
    """Search file and replace any occurrence of pattern with replacement.

    :param file_path: Path of file to be searched.
    :param re.RegexObject search_pattern: Pattern for which to search.
    :param replacement: The string to replace pattern.
    """
    try:
        with open(file_path, "r+") as fil:
            try:
                original = fil.read()
            except UnicodeDecodeError:
                # This was probably a binary file. Skip it.
                return

            replaced = search_pattern.sub(replacement, original)
            if replaced != original:
                fil.seek(0)
                fil.truncate()
                fil.write(replaced)
    except PermissionError as err:
        logger.warning("Unable to open %s for writing: %s", file_path, err)
