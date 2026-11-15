"""Export the API contract without starting the web or simulator processes."""

import argparse
import json
import sys
from pathlib import Path

from apps.api.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write to this path instead of standard output")
    output = parser.parse_args().output
    schema = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(schema)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(schema, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
