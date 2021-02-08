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

import os
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import call

from craft_parts.packages.apt_cache import AptCache

# pylint: disable=missing-class-docstring
# pylint: disable=too-few-public-methods


class TestAptStageCache:
    # This are expensive tests, but is much more valuable than using mocks.
    # When adding tests, consider adding it to test_stage_packages(), or
    # create mocks.
    @unittest.skip("hangs on google spread test with 'Error in function start'")
    def test_stage_packages(self, tmpdir):
        fetch_dir_path = Path(tmpdir, "debs")
        fetch_dir_path.mkdir(exist_ok=True, parents=True)
        stage_cache = Path(tmpdir, "cache")
        stage_cache.mkdir(exist_ok=True, parents=True)

        with AptCache(stage_cache=stage_cache) as apt_cache:
            apt_cache.update()

            package_names = {"pciutils"}
            filtered_names = {"base-files", "libc6", "libkmod2", "libudev1", "zlib1g"}

            apt_cache.mark_packages(package_names)
            apt_cache.unmark_packages(
                required_names=package_names, filtered_names=filtered_names
            )

            marked_packages = apt_cache.get_packages_marked_for_installation()
            assert sorted([name for name, _ in marked_packages]) == [
                "libpci3",
                "pciutils",
            ]

            names = []
            for pkg_name, pkg_version, dl_path in apt_cache.fetch_archives(
                fetch_dir_path
            ):
                names.append(pkg_name)
                assert dl_path.exists()
                assert dl_path.parent == fetch_dir_path
                assert isinstance(pkg_version, str)

            assert sorted(names) == ["libpci3", "pciutils"]


class TestMockedApt:
    def test_stage_cache(self, tmpdir, mocker):
        stage_cache = Path(tmpdir, "cache")
        stage_cache.mkdir(exist_ok=True, parents=True)
        fake_apt = mocker.patch("craft_parts.packages.apt_cache.apt")

        with AptCache(stage_cache=stage_cache) as apt_cache:
            apt_cache.update()

        assert fake_apt.mock_calls == [
            call.apt_pkg.config.set("Apt::Install-Recommends", "False"),
            call.apt_pkg.config.set("Acquire::AllowInsecureRepositories", "False"),
            call.apt_pkg.config.set("Dir::Etc::Trusted", "/etc/apt/trusted.gpg"),
            call.apt_pkg.config.set(
                "Dir::Etc::TrustedParts", "/etc/apt/trusted.gpg.d/"
            ),
            call.apt_pkg.config.clear("APT::Update::Post-Invoke-Success"),
            call.progress.text.AcquireProgress(),
            call.cache.Cache(rootdir=str(stage_cache), memonly=True),
            call.cache.Cache().update(fetch_progress=mock.ANY, sources_list=None),
            call.cache.Cache().close(),
            call.cache.Cache(rootdir=str(stage_cache), memonly=True),
            call.cache.Cache().close(),
        ]

    def test_stage_cache_in_snap(self, tmpdir, mocker):
        fake_apt = mocker.patch("craft_parts.packages.apt_cache.apt")

        stage_cache = Path(tmpdir, "cache")
        stage_cache.mkdir(exist_ok=True, parents=True)

        snap = Path(tmpdir, "snap")
        snap.mkdir(exist_ok=True, parents=True)

        mocker.patch("craft_parts.utils.os_utils.is_snap", return_value=True)

        with mock.patch.dict(os.environ, {"SNAP": str(snap)}), AptCache(
            stage_cache=stage_cache
        ) as apt_cache:
            apt_cache.update()

        assert fake_apt.mock_calls == [
            call.apt_pkg.config.set("Apt::Install-Recommends", "False"),
            call.apt_pkg.config.set("Acquire::AllowInsecureRepositories", "False"),
            call.apt_pkg.config.set("Dir", str(Path(snap, "usr/lib/apt"))),
            call.apt_pkg.config.set(
                "Dir::Bin::methods",
                str(Path(snap, "usr/lib/apt/methods")) + os.sep,
            ),
            call.apt_pkg.config.set(
                "Dir::Bin::solvers::",
                str(Path(snap, "usr/lib/apt/solvers")) + os.sep,
            ),
            call.apt_pkg.config.set(
                "Dir::Bin::apt-key", str(Path(snap, "usr/bin/apt-key"))
            ),
            call.apt_pkg.config.set(
                "Apt::Key::gpgvcommand", str(Path(snap, "usr/bin/gpgv"))
            ),
            call.apt_pkg.config.set("Dir::Etc::Trusted", "/etc/apt/trusted.gpg"),
            call.apt_pkg.config.set(
                "Dir::Etc::TrustedParts", "/etc/apt/trusted.gpg.d/"
            ),
            call.apt_pkg.config.clear("APT::Update::Post-Invoke-Success"),
            call.progress.text.AcquireProgress(),
            call.cache.Cache(rootdir=str(stage_cache), memonly=True),
            call.cache.Cache().update(fetch_progress=mock.ANY, sources_list=None),
            call.cache.Cache().close(),
            call.cache.Cache(rootdir=str(stage_cache), memonly=True),
            call.cache.Cache().close(),
        ]

    def test_host_cache_setup(self, mocker):
        fake_apt = mocker.patch("craft_parts.packages.apt_cache.apt")

        with AptCache() as _:
            pass

        assert fake_apt.mock_calls == [
            call.cache.Cache(rootdir="/"),
            call.cache.Cache().close(),
        ]


class TestAptReadonlyHostCache:
    def test_host_is_package_valid(self):
        with AptCache() as apt_cache:
            assert apt_cache.is_package_valid("apt")
            assert apt_cache.is_package_valid("fake-news-bears") is False

    def test_host_get_installed_packages(self):
        with AptCache() as apt_cache:
            installed_packages = apt_cache.get_installed_packages()
            assert isinstance(installed_packages, dict)
            assert "apt" in installed_packages
            assert "fake-news-bears" not in installed_packages

    def test_host_get_installed_version(self):
        with AptCache() as apt_cache:
            assert isinstance(apt_cache.get_installed_version("apt"), str)
            assert apt_cache.get_installed_version("fake-news-bears") is None
