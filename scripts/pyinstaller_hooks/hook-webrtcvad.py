"""Overrides pyinstaller-hooks-contrib's hook-webrtcvad, which copies the metadata of the
`webrtcvad` distribution. We install `webrtcvad-wheels` (same module, prebuilt wheels), whose
webrtcvad.py reads its own version from that distribution's metadata at import."""

from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
