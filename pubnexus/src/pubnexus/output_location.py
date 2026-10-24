"""Portable exports go to the user's actual Documents known folder."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def known_directory(kind: str) -> Path:
    """Use Windows known folders, including redirected Desktop and Downloads."""
    folders = {
        'documents': ('FDD39AD0-238F-46AF-ADB4-6C85480369C7', 'Documents'),
        'desktop': ('B4BFCC3A-DB2C-424C-B029-7FE99A87C641', 'Desktop'),
        'downloads': ('374DE290-123F-4565-9164-39C4925E467B', 'Downloads'),
    }
    identifier, fallback = folders[kind]
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        import uuid
        class GUID(ctypes.Structure):
            _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD),
                        ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]
        guid = GUID.from_buffer_copy(uuid.UUID(identifier).bytes_le)
        value = ctypes.c_wchar_p()
        try:
            lookup = ctypes.windll.shell32.SHGetKnownFolderPath
            lookup.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                               ctypes.POINTER(ctypes.c_wchar_p)]
            lookup.restype = ctypes.c_long
            # Shortcuts need the redirected path, even when its network is offline.
            if lookup(ctypes.byref(guid), 0x4000, None, ctypes.byref(value)) == 0 and value.value:
                return Path(value.value)
        except (AttributeError, OSError):
            pass
        finally:
            if value:
                free = ctypes.windll.ole32.CoTaskMemFree
                free.argtypes = [ctypes.c_void_p]
                free(ctypes.cast(value, ctypes.c_void_p))
    return Path.home() / fallback


def documents_directory() -> Path:
    return known_directory('documents')


def default_output_dir() -> Path:
    override = os.environ.get('PUBNEXUS_OUTPUT', '').strip()
    return Path(override).expanduser().resolve() if override else documents_directory() / '문서 추출기'
