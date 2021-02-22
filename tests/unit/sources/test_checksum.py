# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2015-2021 Canonical Ltd
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

from pathlib import Path

import pytest

from craft_parts.sources import checksum, errors


@pytest.mark.parametrize(
    "tc_checksum,tc_algorithm,tc_digest",
    [
        ("algorithm/digest", "algorithm", "digest"),
        ("algorithm/dig/est", "algorithm", "dig/est"),
        ("algorithm/", "algorithm", ""),
        ("/digest", "", "digest"),
        ("//", "", "/"),
        ("/", "", ""),
    ],
)
def test_split_checksum_happy(tc_checksum, tc_algorithm, tc_digest):
    algorithm, digest = checksum.split_checksum(tc_checksum)
    assert algorithm == tc_algorithm
    assert digest == tc_digest


@pytest.mark.parametrize("tc_checksum", ["", "something"])
def test_split_checksum_error(tc_checksum):
    with pytest.raises(ValueError) as raised:
        checksum.split_checksum(tc_checksum)
    assert str(raised.value) == f"invalid checksum format: '{tc_checksum}'"


@pytest.mark.parametrize(
    "tc_checksum,tc_checkfile",
    [
        ("md5/9a0364b9e99bb480dd25e1f0284c8555", "content"),
        ("sha1/040f06fd774092478d450774f5ba30c5da78acc8", "content"),
    ],
)
@pytest.mark.usefixtures("new_dir")
def test_verify_checksum_happy(tc_checksum, tc_checkfile):
    Path("checkfile").write_text(tc_checkfile)
    checksum.verify_checksum(tc_checksum, "checkfile")


@pytest.mark.usefixtures("new_dir")
def test_verify_checksum_invalid_algorithm():
    Path("checkfile").write_text("content")
    with pytest.raises(AttributeError) as raised:
        checksum.verify_checksum("invalid/digest", "checkfile")
    assert str(raised.value) == "module 'hashlib' has no attribute 'invalid'"


@pytest.mark.usefixtures("new_dir")
def test_verify_checksum_value_error():
    Path("checkfile").write_text("content")
    with pytest.raises(ValueError) as raised:
        checksum.verify_checksum("invalid", "checkfile")
    assert str(raised.value) == "invalid checksum format: 'invalid'"


# pylint: disable=line-too-long


@pytest.mark.usefixtures("new_dir")
def test_verify_checksum_digest_error():
    Path("checkfile").write_text("content")
    with pytest.raises(errors.ChecksumMismatch) as raised:
        checksum.verify_checksum("md5/digest", "checkfile")
    assert (
        str(raised.value)
        == "Expected the digest to be digest, but it was 9a0364b9e99bb480dd25e1f0284c8555."
    )
