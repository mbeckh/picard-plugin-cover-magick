# Copyright 2026 Michael Beckh (mbeckh)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""API interface to bootstrap Monkey for a Picard plugin."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Concatenate,
    ParamSpec,
    TypeVar,
)

from packaging.version import Version

from picard import tagger_instance
from picard.plugin3.plugin import Plugin


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from picard.plugin3.manager import PluginManager


_API_VERSION = Version('1.0.0')
_UUID = '8cf02c96-a949-4c1e-9ef4-20898c5783c7'

if TYPE_CHECKING:
    C = TypeVar('C')
    P = ParamSpec('P')
    R = TypeVar('R')

    type PatchWrapper[**P, R] = Callable[Concatenate[Callable[P, R], P], R]
    type ClassMethodPatchWrapper[C, **P, R] = Callable[Concatenate[Callable[Concatenate[type[C], P], R], type[C], P], R]

    def patch[C, **P, R](
        target: ModuleType | type[C],
        wrapped: Callable[P, R] | property | staticmethod[P, R],
        priority: int = 0,
        property_type: str = 'fget',
    ) -> Callable[[PatchWrapper[P, R]], PatchWrapper[P, R]]:
        """Install a wrapper around a function.

        Args:
            target: The module or type to patch.
            wrapped: The function or property to patch.
            priority: Wrapper execution order from highest on the outside to lowest one the inside.
            property_type: Property accessor to patch (fget, fset or fdel).

        Raises:
            AttributeError: If the target function cannot be found.
        """
        ...

    def patch_classmethod[C, **P, R](
        target: type[C],
        wrapped: Callable[P, R],
        priority: int = 0,
        property_type: str = 'fget',
    ) -> Callable[[ClassMethodPatchWrapper[C, P, R]], ClassMethodPatchWrapper[C, P, R]]:
        """Install a wrapper around a class method.

        Same as patch, but class methods require special handling to account for the class parameter.

        Args:
            target: The module or type to patch.
            wrapped: The function or property to patch.
            priority: Wrapper execution order from highest on the outside to lowest one the inside.
            property_type: Property accessor to patch (fget, fset or fdel).

        Raises:
            AttributeError: If the target function cannot be found.
        """
        ...

    def unpatch[C, **P, R](
        target: ModuleType | type[C],
        wrapped: Callable[P, R] | property | staticmethod[P, R],
        wrapper: PatchWrapper[P, R] | ClassMethodPatchWrapper[C, P, R],
        property_type: str = 'fget',
    ) -> bool:
        """Remove a previously installed wrapper.

        Args:
            target: The module or type to unpatch.
            wrapped: The patched function, method or property.
            wrapper: The wrapper function to remove.
            property_type: Property accessor (fget, fset or fdel).

        Returns:
            True if the wrapper was found and removed.
        """
        ...

    def unpatch_module(module: str) -> None:
        """Remove all patches for functions from a module.

        Args:
            module: The name of the module container the wrapper functions.
        """
        ...


def _bootstrap_monkey() -> None:
    """Bootstrap Monkey plugin, i.e. load module and import decorators and functions.

    Raises:
        ModuleNotFoundError: If plugin is not installed, cannot be loaded or has wrong version.
    """
    # PluginApi might not yet be available, so tagger_instance() is our only choice
    manager: PluginManager | None = tagger_instance().get_plugin_manager()
    if (
        manager
        and (plugin := manager.find_plugin(_UUID))
        and (isinstance(plugin, Plugin))
        and (monkey := (plugin._module or plugin.load_module()))
        and (plugin_api_version := getattr(monkey, 'API_VERSION', None))
    ):
        if plugin_api_version.major != _API_VERSION.major or plugin_api_version.minor < _API_VERSION.minor:
            # Update either installed Monkey plugin or client plugin.
            raise ModuleNotFoundError(
                f"Monkey plugin requires API {plugin_api_version} but client API is {_API_VERSION}.", name=plugin.name()
            )

        for name in monkey.__all__:
            globals()[name] = getattr(monkey, name)
        return

    raise ModuleNotFoundError("Could not locate or load Monkey plugin.", name=_UUID)


_bootstrap_monkey()
