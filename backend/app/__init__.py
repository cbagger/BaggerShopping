"""Kurv backend package.

Runtime behavior lives in explicit service/provider modules. This package
initializer deliberately performs no monkeypatching or global function
replacement.
"""

from __future__ import annotations


async def _original_fetch_all_publications(*args, **kwargs):
    """Temporary compatibility shim for raw flyer consumers.

    New code should import ``flyer_publications.fetch_raw_publications``
    directly. Keeping this lazy during the hardening transition avoids import
    cycles without restoring any startup patching.
    """
    from .flyer_publications import fetch_raw_publications

    return await fetch_raw_publications(*args, **kwargs)
