from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skynet.settings.loader import write_component_env_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Render effective settings into a .env file.")
    parser.add_argument(
        "--component",
        action="append",
        dest="components",
        required=True,
        help="Component to include: control, gateway, or agent. Repeat for multiple components.",
    )
    parser.add_argument("--output", required=True, help="Output .env file path.")
    args = parser.parse_args()

    output = Path(args.output)
    write_component_env_file(output, args.components)
    print(f"Wrote effective settings env: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
