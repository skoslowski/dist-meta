#!/usr/bin/env python3
#
#  distributions.py
"""
Iterate over installed distributions.
"""
#
#  Copyright © 2021 Dominic Davis-Foster <dominic@davis-foster.co.uk>
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
#  EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#  MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
#  IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
#  DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
#  OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
#  OR OTHER DEALINGS IN THE SOFTWARE.
#
#  Parts of iter_distributions based on https://github.com/takluyver/entrypoints
#  Copyright (c) 2015 Thomas Kluyver and contributors
#  MIT Licensed
#

# stdlib
import posixpath
import re
import sys
from typing import Dict, Iterable, Iterator, NamedTuple, Optional, Tuple, Type, TypeVar, Union, cast

# 3rd party
import handy_archives
from domdf_python_tools.paths import PathPlus
from domdf_python_tools.typing import PathLike
from domdf_python_tools.utils import divide
from packaging.utils import InvalidWheelFilename, canonicalize_name
from packaging.version import Version

# this package
from dist_meta import metadata, wheel
from dist_meta.metadata_mapping import MetadataMapping

__all__ = [
		"Distribution",
		"WheelDistribution",
		"iter_distributions",
		"get_distribution",
		"DistributionNotFoundError",
		"_D",
		"_WD",
		]

_D = TypeVar("_D", bound="Distribution")
_WD = TypeVar("_WD", bound="WheelDistribution")


class Distribution(NamedTuple):
	"""
	Represents an installed Python distribution.

	:param name: The name of the distribution.
	"""

	#: The name of the distribution. No normalization is performed.
	name: str

	#: The version number of the distribution.
	version: Version

	#: The path to the ``*.dist-info`` directory in the file system.
	path: PathPlus

	@classmethod
	def from_path(cls: Type[_D], path: PathLike) -> _D:
		"""
		Construct a :class:`~.Distribution` from a filesystem path to the ``*.dist-info`` directory.

		:param path:
		"""

		path = PathPlus(path)
		distro_name_version = path.stem
		name, version = divide(distro_name_version, '-')
		return cls(name, Version(version), path)

	def read_file(self, filename: str) -> str:
		"""
		Read a file from the ``*.dist-info`` directory and return its content.

		:param filename:
		"""

		return (self.path / filename).read_text()

	def has_file(self, filename: str) -> bool:
		"""
		Returns whether the ``*.dist-info`` directory contains a file named ``filename``.

		:param filename:
		"""

		return (self.path / filename).is_file()

	def get_entry_points(self) -> Dict[str, Dict[str, str]]:  # -> EntryPointMap
		"""
		Returns a mapping of entry point groups to entry points.

		Entry points in the group are contained in a dictionary mapping entry point names to objects.

		:class:`dist_meta.entry_points.EntryPoint` objects can be constructed as follows:

		.. code-block:: python

			for name, epstr in distro.get_entry_points().get("console_scripts", {}):
				EntryPoint(name, epstr)
		"""

		# this package
		from dist_meta import entry_points

		if self.has_file("entry_points.txt"):
			return entry_points.loads(self.read_file("entry_points.txt"))
		else:
			return {}

	def get_metadata(self) -> MetadataMapping:
		"""
		Returns the content of the ``*.dist-info/METADATA`` file.
		"""

		return metadata.loads(self.read_file("METADATA"))

	def get_wheel(self) -> Optional[MetadataMapping]:
		"""
		Returns the content of the ``*.dist-info/WHEEL`` file, or :py:obj:`None` if the file does not exist.

		The file will only be present if the distribution was installed from a :pep:`wheel <427>`.
		"""  # noqa: RST399

		if self.has_file("WHEEL"):
			return wheel.loads(self.read_file("WHEEL"))
		else:
			return None

	def __repr__(self):
		"""
		Returns a string representation of the :class:`~.Distribution`.
		"""

		return f"<{self.__class__.__name__}({self.name!r}, {self.version!r})>"


class WheelDistribution(NamedTuple):
	"""
	Represents a Python distribution in :pep:`wheel <427>` form.

	:param name: The name of the distribution.


	A :class:`~.WheelDistribution` can be used as a contextmanager,
	which will close the underlying :class:`zipfile.ZipFile` when exiting
	the :keyword:`with` block.
	"""  # noqa: RST399

	#: The name of the distribution. No normalization is performed.
	name: str

	#: The version number of the distribution.
	version: Version

	#: The path to the ``.whl`` file.
	path: PathPlus

	#: The opened zip file.
	wheel_zip: handy_archives.ZipFile

	@classmethod
	def from_path(cls: Type[_WD], path: PathLike, **kwargs) -> _WD:
		r"""
		Construct a :class:`~.WheelDistribution` from a filesystem path to the ``.whl`` file.

		:param path:
		:param \*\*kwargs: Additional keyword arguments passed to :class:`zipfile.ZipFile`.
		"""

		path = PathPlus(path)
		name, version, *_ = _parse_wheel_filename(path)
		wheel_zip = handy_archives.ZipFile(path, 'r', **kwargs)

		return cls(name, version, path, wheel_zip)

	def __enter__(self: _WD) -> _WD:
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.wheel_zip.close()

	def read_file(self, filename: str) -> str:
		"""
		Read a file from the ``*.dist-info`` directory and return its content.

		:param filename:
		"""

		dist_info = f"{self.name}-{self.version}.dist-info"
		return self.wheel_zip.read_text(posixpath.join(dist_info, filename))

	def has_file(self, filename: str) -> bool:
		"""
		Returns whether the ``*.dist-info`` directory contains a file named ``filename``.

		:param filename:
		"""

		dist_info = f"{self.name}-{self.version}.dist-info"
		return posixpath.join(dist_info, filename) in self.wheel_zip.namelist()

	def get_entry_points(self) -> Dict[str, Dict[str, str]]:  # -> EntryPointMap
		"""
		Returns a mapping of entry point groups to entry points.

		Entry points in the group are contained in a dictionary mapping entry point names to objects.

		:class:`dist_meta.entry_points.EntryPoint` objects can be constructed as follows:

		.. code-block:: python

			for name, epstr in distro.get_entry_points().get("console_scripts", {}):
				EntryPoint(name, epstr)
		"""

		# this package
		from dist_meta import entry_points

		if self.has_file("entry_points.txt"):
			return entry_points.loads(self.read_file("entry_points.txt"))
		else:
			return {}

	def get_metadata(self) -> MetadataMapping:
		"""
		Returns the content of the ``*.dist-info/METADATA`` file.
		"""

		return metadata.loads(self.read_file("METADATA"))

	def get_wheel(self) -> MetadataMapping:
		"""
		Returns the content of the ``*.dist-info/WHEEL`` file.

		:raises FileNotFoundError: if the file does not exist.
		"""

		return wheel.loads(self.read_file("WHEEL"))

	def __repr__(self):
		"""
		Returns a string representation of the :class:`~.WheelDistribution`.
		"""

		return f"<{self.__class__.__name__}({self.name!r}, {self.version!r})>"


DistributionType = Union[Distribution, WheelDistribution]


def iter_distributions(path: Optional[Iterable[PathLike]] = None) -> Iterator[Distribution]:
	"""
	Returns an iterator over installed distributions on ``path``.

	:param path: The directories entries to search for distributions in.
	:default path: :py:data:`sys.path`
	"""

	if path is None:  # pragma: no cover
		path = sys.path

	# Distributions found earlier in path will shadow those with the same name found later.
	# If these distributions used different module names, it may actually be possible to import both,
	# but in most cases this shadowing will be correct.
	distro_names_seen = set()

	for folder in map(PathPlus, path):
		if not folder.is_dir():
			continue

		for dist_info in folder.glob("*.dist-info"):
			distro = Distribution.from_path(dist_info)

			normalized_name = canonicalize_name(distro.name)

			if normalized_name in distro_names_seen:
				continue

			distro_names_seen.add(normalized_name)

			yield distro


def get_distribution(
		name: str,
		path: Optional[Iterable[PathLike]] = None,
		) -> Distribution:
	"""
	Returns a :class:`~.Distribution` instance for the distribution with the given name.

	:param name:
	:param path: The directories entries to search for distributions in.
	:default path: :py:data:`sys.path`

	:rtype:
	"""

	for distro in iter_distributions(path=path):
		if canonicalize_name(distro.name) == canonicalize_name(name):
			return distro

	raise DistributionNotFoundError(name)


class DistributionNotFoundError(ValueError):
	"""
	Raised when a distribution cannot be located.
	"""


def _parse_wheel_filename(filename: PathPlus) -> Tuple[str, Version]:
	# From https://github.com/pypa/packaging
	# This software is made available under the terms of *either* of the licenses
	# found in LICENSE.APACHE or LICENSE.BSD. Contributions to this software is made
	# under the terms of *both* these licenses.

	if not filename.suffix == ".whl":
		raise InvalidWheelFilename(f"Invalid wheel filename (extension must be '.whl'): {filename}")

	stem = filename.stem
	dashes = stem.count('-')
	if dashes not in (4, 5):
		raise InvalidWheelFilename(f"Invalid wheel filename (wrong number of parts): {filename}")

	parts = stem.split('-', dashes - 2)
	name = parts[0]
	# See PEP 427 for the rules on escaping the project name
	if "__" in name or re.match(r"^[\w\d._]*$", name, re.UNICODE) is None:
		raise InvalidWheelFilename(f"Invalid project name: {name!r}")

	version = Version(parts[1])

	return name, version
