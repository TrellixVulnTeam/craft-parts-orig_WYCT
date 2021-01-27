# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2017-2021 Canonical Ltd
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

"""Implement the tar source handler."""

import logging
import os
import shutil
from typing import Optional

from craft_parts.utils import file_utils

from .base import Cache

logger = logging.getLogger(__name__)


class FileCache(Cache):
    """Generic file cache."""

    def __init__(self, *, namespace: str = "files") -> None:
        """Create a FileCache under namespace.

        :param str namespace: set the namespace for the cache
                              (default: "files").
        """
        super().__init__()
        self.file_cache = os.path.join(self.cache_root, namespace)

    def cache(self, *, filename: str, algorithm: str, digest: str) -> Optional[str]:
        """Cache a file revision with hash in XDG cache, unless it already exists.
        :param str filename: path to the file to cache.
        :param str algorithm: algorithm used to calculate the hash as
                              understood by hashlib.
        :param str digest: hash for filename calculated with algorithm.
        :returns: path to cached file.
        """
        # First we verify
        calculated_hash = file_utils.calculate_hash(filename, algorithm=algorithm)
        if calculated_hash != digest:
            logger.warning(
                "Skipping caching of %s as the expected hash does not match "
                "the one provided",
                filename,
            )
            return None
        cached_file_path = os.path.join(self.file_cache, algorithm, digest)
        os.makedirs(os.path.dirname(cached_file_path), exist_ok=True)
        try:
            if not os.path.isfile(cached_file_path):
                # this must not be hard-linked, as rebuilding a snap
                # with changes should invalidate the cache, hence avoids
                # using fileutils.link_or_copy.
                shutil.copyfile(filename, cached_file_path)
        except OSError:
            logger.warning("Unable to cache file %s.", cached_file_path)
            return None
        return cached_file_path

    def get(self, *, algorithm: str, digest: str):
        """Get the filepath which matches the hash calculated with algorithm.

        :param str algorithm: algorithm used to calculate the hash as
                              understood by hashlib.
        :param str digest: hash for filename calculated with algorithm.
        :returns: path to cached file.
        """
        cached_file_path = os.path.join(self.file_cache, algorithm, digest)
        if os.path.exists(cached_file_path):
            logger.debug("Cache hit for hash %s", digest)
            return cached_file_path

        return None
