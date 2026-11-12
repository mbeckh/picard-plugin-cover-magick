# API interface to make Monkey available in a Picard plugin.
#
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

from collections.abc import Callable
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Concatenate,
    ParamSpec,
    TypeAlias,
    TypeVar,
)


_NAME = 'picard-plugin-monkey'
_UUID = '8cf02c96-a949-4c1e-9ef4-20898c5783c7'

C = TypeVar('C')
P = ParamSpec('P')
R = TypeVar('R')

PatchWrapper: TypeAlias = Callable[Concatenate[Callable[P, R], P], R]
ClassMethodPatchWrapper: TypeAlias = Callable[Concatenate[Callable[Concatenate[type[C], P], R], type[C], P], R]

if TYPE_CHECKING:

    def patch(
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

    def patch_classmethod(
        target: type[C],
        wrapped: Callable[P, R] | classmethod[C, P, R],
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

    def unpatch(
        target: ModuleType | type[C],
        wrapped: Callable[P, R] | property | staticmethod[P, R] | classmethod[C, P, R],
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


def _import_monkey():
    from picard import tagger_instance
    from picard.plugin3.manager import PluginManager

    # PluginApi might not yet be available, so tagger_instance() is our only choice
    manager: PluginManager | None = tagger_instance().get_plugin_manager()
    if manager:
        plugin = manager.plugin_id_to_plugin(_NAME)
        if plugin and plugin.uuid == _UUID:
            if plugin._module:
                monkey = plugin._module
            else:
                monkey = plugin.load_module()
            if monkey:
                for name in monkey.__all__:
                    globals()[name] = getattr(monkey, name)
                return

    raise ModuleNotFoundError(name=_NAME)


_import_monkey()
