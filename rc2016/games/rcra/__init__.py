"""Ratchet & Clank: Rift Apart (Steam PC build): archive reader and decoders.

The game folder holds ``toc`` (asset id -> archive, offset, size, optional 36-byte asset header),
``dag`` (asset paths, types and dependency lists) and ``d/`` (DSAR block-compressed archives).
Layouts are documented in docs/rift-apart.md; ALERT ``dat1lib`` and RCRA-Forge were the references,
and the readers here were checked against the installed files.
"""
