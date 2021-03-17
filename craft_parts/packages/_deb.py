# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2020 Canonical Ltd
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

"""Support for deb files."""

import fileinput
import functools
import logging
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import List, Set, Tuple

from xdg import BaseDirectory  # type: ignore

from craft_parts.utils import file_utils, os_utils

from . import errors
from ._base import BaseRepository, get_pkg_name_parts

if sys.platform == "linux":
    # Ensure importing works on non-Linux.
    from .apt_cache import AptCache

logger = logging.getLogger(__name__)


_HASHSUM_MISMATCH_PATTERN = re.compile(r"(E:Failed to fetch.+Hash Sum mismatch)+")
_DEFAULT_FILTERED_STAGE_PACKAGES: List[str] = [
    "adduser",
    "apt",
    "apt-utils",
    "base-files",
    "base-passwd",
    "bash",
    "bsdutils",
    "coreutils",
    "dash",
    "debconf",
    "debconf-i18n",
    "debianutils",
    "diffutils",
    "dmsetup",
    "dpkg",
    "e2fslibs",
    "e2fsprogs",
    "file",
    "findutils",
    "gcc-4.9-base",
    "gcc-5-base",
    "gnupg",
    "gpgv",
    "grep",
    "gzip",
    "hostname",
    "init",
    "initscripts",
    "insserv",
    "libacl1",
    "libapparmor1",
    "libapt",
    "libapt-inst1.5",
    "libapt-pkg4.12",
    "libattr1",
    "libaudit-common",
    "libaudit1",
    "libblkid1",
    "libbz2-1.0",
    "libc-bin",
    "libc6",
    "libcap2",
    "libcap2-bin",
    "libcomerr2",
    "libcryptsetup4",
    "libdb5.3",
    "libdebconfclient0",
    "libdevmapper1.02.1",
    "libgcc1",
    "libgcrypt20",
    "libgpg-error0",
    "libgpm2",
    "libkmod2",
    "liblocale-gettext-perl",
    "liblzma5",
    "libmagic1",
    "libmount1",
    "libncurses5",
    "libncursesw5",
    "libpam-modules",
    "libpam-modules-bin",
    "libpam-runtime",
    "libpam0g",
    "libpcre3",
    "libprocps3",
    "libreadline6",
    "libselinux1",
    "libsemanage-common",
    "libsemanage1",
    "libsepol1",
    "libslang2",
    "libsmartcols1",
    "libss2",
    "libstdc++6",
    "libsystemd0",
    "libtext-charwidth-perl",
    "libtext-iconv-perl",
    "libtext-wrapi18n-perl",
    "libtinfo5",
    "libudev1",
    "libusb-0.1-4",
    "libustr-1.0-1",
    "libuuid1",
    "locales",
    "login",
    "lsb-base",
    "makedev",
    "manpages",
    "manpages-dev",
    "mawk",
    "mount",
    "multiarch-support",
    "ncurses-base",
    "ncurses-bin",
    "passwd",
    "perl-base",
    "procps",
    "readline-common",
    "sed",
    "sensible-utils",
    "systemd",
    "systemd-sysv",
    "sysv-rc",
    "sysvinit-utils",
    "tar",
    "tzdata",
    "ubuntu-keyring",
    "udev",
    "util-linux",
    "zlib1g",
]


@functools.lru_cache(maxsize=256)
def _run_dpkg_query_search(file_path: str) -> str:
    try:
        output = (
            subprocess.check_output(
                ["dpkg-query", "-S", os.path.join(os.path.sep, file_path)],
                stderr=subprocess.STDOUT,
                env=dict(LANG="C.UTF-8"),
            )
            .decode()
            .strip()
        )
    except subprocess.CalledProcessError as call_error:
        logger.debug("Error finding package for %s: %s", file_path, str(call_error))
        raise errors.FileProviderNotFound(file_path=file_path) from call_error

    # Remove diversions
    provides_output = [p for p in output.splitlines() if not p.startswith("diversion")][
        0
    ]
    return provides_output.split(":")[0]


@functools.lru_cache(maxsize=256)
def _run_dpkg_query_list_files(package_name: str) -> Set[str]:
    output = (
        subprocess.check_output(["dpkg", "-L", package_name])
        .decode(sys.getfilesystemencoding())
        .strip()
        .split()
    )

    return {i for i in output if ("lib" in i and os.path.isfile(i))}


def _get_dpkg_list_path(base: str) -> pathlib.Path:
    return pathlib.Path(f"/snap/{base}/current/usr/share/snappy/dpkg.list")


def get_packages_in_base(*, base: str) -> List[str]:
    """Get the list of packages for the given base."""

    # We do not want to break what we already have.
    if base in ("core", "core16", "core18"):
        return _DEFAULT_FILTERED_STAGE_PACKAGES

    base_package_list_path = _get_dpkg_list_path(base)
    if not base_package_list_path.exists():
        return list()

    # Lines we care about in dpkg.list had the following format:
    # ii adduser 3.118ubuntu1 all add and rem
    package_list = list()
    with fileinput.input(str(base_package_list_path)) as f:
        for line in f:
            if not line.startswith("ii "):
                continue
            package_list.append(line.split()[1])

    # format of package_list is <package_name>[:<architecture>]
    return package_list


class Ubuntu(BaseRepository):
    """Repository management for Ubuntu packages."""

    @classmethod
    def get_package_libraries(cls, package_name: str) -> Set[str]:
        return _run_dpkg_query_list_files(package_name)

    @classmethod
    def get_package_for_file(cls, file_path: str) -> str:
        return _run_dpkg_query_search(file_path)

    @classmethod
    def get_packages_for_source_type(cls, source_type):
        if source_type == "bzr":
            packages = {"bzr"}
        elif source_type == "git":
            packages = {"git"}
        elif source_type == "tar":
            packages = {"tar"}
        elif source_type in ["hg", "mercurial"]:
            packages = {"mercurial"}
        elif source_type == ["svn", "subversion"]:
            packages = {"subversion"}
        elif source_type == "rpm2cpio":
            packages = {"rpm2cpio"}
        elif source_type == "7zip":
            packages = {"p7zip-full"}
        else:
            packages = set()

        return packages

    @classmethod
    def refresh_build_packages(cls) -> None:
        try:
            cmd = ["sudo", "--preserve-env", "apt-get", "update"]
            logger.debug("Executing: %s", cmd)
            subprocess.check_call(cmd)
        except subprocess.CalledProcessError as call_error:
            raise errors.CacheUpdateFailed("failed to run apt update") from call_error

    @classmethod
    def _check_if_all_packages_installed(cls, package_names: List[str]) -> bool:
        """Check if all given packages are installed.

        Will check versions if using <pkg_name>=<pkg_version> syntax parsed by
        get_pkg_name_parts().  Used as an optimization to skip installation
        and cache refresh if dependencies are already satisfied.

        :return True if _all_ packages are installed (with correct versions).
        """

        with AptCache() as apt_cache:
            for package in package_names:
                pkg_name, pkg_version = get_pkg_name_parts(package)
                installed_version = apt_cache.get_installed_version(
                    pkg_name, resolve_virtual_packages=True
                )

                if installed_version is None or (
                    pkg_version is not None and installed_version != pkg_version
                ):
                    return False

        return True

    @classmethod
    def _get_packages_marked_for_installation(
        cls, package_names: List[str]
    ) -> List[Tuple[str, str]]:
        with AptCache() as apt_cache:
            try:
                apt_cache.mark_packages(set(package_names))
            except errors.PackageNotFound as error:
                raise errors.BuildPackageNotFound(error.package_name)

            return apt_cache.get_packages_marked_for_installation()

    @classmethod
    def install_build_packages(cls, package_names: List[str]) -> List[str]:
        """Install packages on the host required to build.

        :param package_names: a list of package names to install.
        :type package_names: a list of strings.
        :return: a list with the packages installed and their versions.
        :rtype: list of strings.
        :raises craft_parts.packages.errors.BuildPackageNotFound:
            if one of the packages was not found.
        :raises craft_parts.packages.errors.PackageBroken:
            if dependencies for one of the packages cannot be resolved.
        :raises craft_parts.packages.errors.BuildPackagesNotInstalled:
            if installing the packages on the host failed.
        """

        if not package_names:
            return []

        install_required = False
        package_names = sorted(package_names)

        logger.debug("Requested build-packages: %s", package_names)

        # Ensure we have an up-to-date cache first if we will have to
        # install anything.
        if not cls._check_if_all_packages_installed(package_names):
            install_required = True
            # refresh the build package list before planning for consistency
            # cls.refresh_build_packages()

        marked_packages = cls._get_packages_marked_for_installation(package_names)
        packages = [f"{name}={version}" for name, version in sorted(marked_packages)]

        if install_required:
            cls._install_packages(packages)
        else:
            logger.debug("Requested build-packages already installed: %s", packages)

        return packages

    @classmethod
    def _install_packages(cls, package_names: List[str]) -> None:
        logger.info("Installing build dependencies: %s", " ".join(package_names))
        env = os.environ.copy()
        env.update(
            {
                "DEBIAN_FRONTEND": "noninteractive",
                "DEBCONF_NONINTERACTIVE_SEEN": "true",
                "DEBIAN_PRIORITY": "critical",
            }
        )

        apt_command = [
            "sudo",
            "--preserve-env",
            "apt-get",
            "--no-install-recommends",
            "-y",
            "--allow-downgrades",
        ]
        if not os_utils.is_dumb_terminal():
            apt_command.extend(["-o", "Dpkg::Progress-Fancy=1"])
        apt_command.append("install")

        try:
            subprocess.check_call(apt_command + package_names, env=env)
        except subprocess.CalledProcessError as err:
            raise errors.BuildPackagesNotInstalled(packages=package_names) from err

        versionless_names = [get_pkg_name_parts(p)[0] for p in package_names]
        try:
            subprocess.check_call(
                ["sudo", "apt-mark", "auto"] + versionless_names, env=env
            )
        except subprocess.CalledProcessError as err:
            logger.warning("Impossible to mark packages as auto-installed: %s", err)

    @classmethod
    def fetch_stage_packages(
        cls,
        *,
        application_name: str,
        package_names: List[str],
        base: str,
        stage_packages_path: pathlib.Path,
        target_arch: str,
        list_only: bool = False,
    ) -> List[str]:
        logger.debug("Requested stage-packages: %s", sorted(package_names))

        if not package_names:
            return []

        installed: Set[str] = set()

        if not list_only:
            stage_packages_path.mkdir(exist_ok=True)

        stage_cache_dir, deb_cache_dir = get_cache_dirs(application_name)

        with AptCache(
            stage_cache=stage_cache_dir, stage_cache_arch=target_arch
        ) as apt_cache:
            filter_packages = set(get_packages_in_base(base=base))
            apt_cache.mark_packages(set(package_names))
            apt_cache.unmark_packages(
                required_names=set(package_names), filtered_names=filter_packages
            )

            if list_only:
                marked_packages = apt_cache.get_packages_marked_for_installation()
                installed = {
                    f"{name}={version}" for name, version in sorted(marked_packages)
                }
            else:
                for pkg_name, pkg_version, dl_path in apt_cache.fetch_archives(
                    deb_cache_dir
                ):
                    logger.debug("Extracting stage package: %s", pkg_name)
                    installed.add(f"{pkg_name}={pkg_version}")
                    file_utils.link_or_copy(
                        str(dl_path), str(stage_packages_path / dl_path.name)
                    )

        return sorted(installed)

    @classmethod
    def update_package_list(cls, *, application_name: str, target_arch: str):
        """Refresh the list of packages available in the repository."""

        stage_cache_dir, _ = get_cache_dirs(application_name)

        with AptCache(
            stage_cache=stage_cache_dir, stage_cache_arch=target_arch
        ) as apt_cache:
            apt_cache.update()

    @classmethod
    def unpack_stage_packages(
        cls, *, stage_packages_path: pathlib.Path, install_path: pathlib.Path
    ) -> None:
        for pkg_path in stage_packages_path.glob("*.deb"):
            with tempfile.TemporaryDirectory(
                suffix="deb-extract", dir=install_path.parent
            ) as extract_dir:
                # Extract deb package.
                cls._extract_deb(pkg_path, extract_dir)
                # Mark source of files.
                marked_name = cls._extract_deb_name_version(pkg_path)
                cls._mark_origin_stage_package(extract_dir, marked_name)
                # Stage files to install_dir.
                file_utils.link_or_copy_tree(extract_dir, install_path.as_posix())
        cls.normalize(str(install_path))

    @classmethod
    def build_package_is_valid(cls, package_name) -> bool:
        with AptCache() as apt_cache:
            return apt_cache.is_package_valid(package_name)

    @classmethod
    def is_package_installed(cls, package_name) -> bool:
        with AptCache() as apt_cache:
            return apt_cache.get_installed_version(package_name) is not None

    @classmethod
    def get_installed_packages(cls) -> List[str]:
        with AptCache() as apt_cache:
            return [
                f"{pkg_name}={pkg_version}"
                for pkg_name, pkg_version in apt_cache.get_installed_packages().items()
            ]

    @classmethod
    def _extract_deb_name_version(cls, deb_path: pathlib.Path) -> str:
        try:
            output = subprocess.check_output(
                ["dpkg-deb", "--show", "--showformat=${Package}=${Version}", deb_path]
            )
        except subprocess.CalledProcessError as err:
            raise errors.UnpackError(deb_path) from err

        return output.decode().strip()

    @classmethod
    def _extract_deb(cls, deb_path: pathlib.Path, extract_dir: str) -> None:
        """Extract deb and return `<package-name>=<version>`."""
        try:
            subprocess.check_call(["dpkg-deb", "--extract", deb_path, extract_dir])
        except subprocess.CalledProcessError as err:
            raise errors.UnpackError(deb_path) from err


def get_cache_dirs(name: str):
    """Return the paths to the stage and deb cache directories."""

    stage_cache_dir = pathlib.Path(
        BaseDirectory.save_cache_path(name, "craft-parts", "stage-packages")
    )

    deb_cache_dir = pathlib.Path(
        BaseDirectory.save_cache_path(name, "craft-parts", "download")
    )

    return (stage_cache_dir, deb_cache_dir)
