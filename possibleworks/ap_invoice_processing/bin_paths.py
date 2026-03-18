# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Binary path resolver for system tools used by the AI processing pipeline.

Docker containers and restricted-PATH environments often hide /usr/bin from
Python subprocesses. This module probes well-known install locations and
returns the directory containing the binary, ready to pass as `*_path=`
kwargs to libraries like pdf2image.

Usage:
    from possibleworks.ap_invoice_processing.bin_paths import get_bin_dir

    poppler_path = get_bin_dir("pdftoppm")   # for pdf2image
    images = convert_from_bytes(data, poppler_path=poppler_path)
"""

import os

# Ordered list of directories to probe. Add new locations here as needed.
_SEARCH_DIRS = [
    "/usr/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",       # macOS (Apple Silicon + Intel)
    "/opt/homebrew/opt/poppler/bin",  # macOS Homebrew explicit poppler prefix
    "/usr/lib/poppler",
    "/snap/bin",
]


def get_bin_dir(binary_name: str) -> str | None:
    """
    Return the directory containing `binary_name`, or None if not found.

    The returned value is ready to pass as a `*_path` kwarg (e.g.
    `poppler_path`) to libraries that need an explicit binary directory.

    Args:
        binary_name: Executable filename, e.g. "pdftoppm", "ffmpeg".

    Returns:
        Directory path string if found, None otherwise.
    """
    for directory in _SEARCH_DIRS:
        if os.path.isfile(os.path.join(directory, binary_name)):
            return directory
    return None


# Pre-resolved constants — imported directly when the path is always the same.
POPPLER_PATH = get_bin_dir("pdftoppm")
