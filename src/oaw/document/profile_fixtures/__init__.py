"""Packaged self-check corpus for ``oaw doctor``'s parser-integrity group.

Each subdirectory holds a ``source.md`` (read as bytes, then decoded as
UTF-8) and an ``expect.json`` describing a subset of
:class:`~oaw.document.model.NoteDocument` to assert, in the same shape as
the larger development-time corpus under ``tests/fixtures/markdown_profile``
(see that tree's own fixtures for the schema this mirrors). This is a small,
representative subset -- not a replacement for the full corpus -- chosen so
it ships inside the installed wheel and can be read back with
``importlib.resources`` at runtime by :mod:`oaw.doctor`, without depending on
a source checkout being present on disk.

This package is data-only. Nothing here imports ``oaw.document`` machinery;
:mod:`oaw.doctor` is the sole reader, via ``importlib.resources.files``.
"""

from __future__ import annotations
