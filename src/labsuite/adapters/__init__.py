"""The adapter layer: swappable interfaces for each system in the stack.

Import the interfaces from here::

    from labsuite.adapters import (
        IdentityProvider, DirectoryProvider, StorageProvider, ComputeProvider,
    )

The in-memory reference adapters live in the top-level modules
(``labsuite.okta``, ``labsuite.active_directory``, ``labsuite.truenas``,
``labsuite.proxmox``); the real-system skeletons live in
``labsuite.adapters.live``.
"""

from labsuite.adapters.base import (
    ComputeProvider,
    DirectoryProvider,
    IdentityProvider,
    StorageProvider,
)

__all__ = [
    "IdentityProvider",
    "DirectoryProvider",
    "StorageProvider",
    "ComputeProvider",
]
