from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skynet.prompt_library import list_prompt_refs, prompt_root


def main() -> int:
    root = prompt_root()
    print(f"Prompt root: {root}")
    refs = list_prompt_refs()
    print(f"Prompt files: {len(refs)}")
    for ref in refs:
        print(ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
