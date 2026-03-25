"""Triage known Paramiko/Cryptography deprecation warnings for gateway test environments."""

from __future__ import annotations

import re
import sys
import warnings


ALLOWED_WARNING_PATTERNS = (
    re.compile(r"TripleDES has been moved .* will be removed .*", re.IGNORECASE),
    re.compile(r"Blowfish has been moved .* will be removed .*", re.IGNORECASE),
)


def _is_allowed_warning(message: str) -> bool:
    return any(pattern.search(message) for pattern in ALLOWED_WARNING_PATTERNS)


def main() -> int:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import paramiko  # noqa: F401

    unexpected: list[str] = []
    allowed_messages: list[str] = []
    for warning_record in caught:
        category_name = getattr(warning_record.category, "__name__", "")
        message = str(warning_record.message)
        if category_name == "CryptographyDeprecationWarning" and _is_allowed_warning(message):
            allowed_messages.append(message)
            continue
        unexpected.append(f"{category_name}: {message}")

    if unexpected:
        print("Unexpected gateway import warnings detected:")
        for item in unexpected:
            print(f"- {item}")
        return 1

    if allowed_messages:
        unique = []
        for item in allowed_messages:
            if item not in unique:
                unique.append(item)
        print("Gateway warning triage passed with known Paramiko/Cryptography warnings:")
        for item in unique:
            print(f"- {item}")
        return 0

    print("Gateway warning triage passed with no Paramiko import warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
