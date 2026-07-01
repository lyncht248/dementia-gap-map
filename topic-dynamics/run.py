#!/usr/bin/env python3
"""Entry point for the Track A pipeline.

The workspace folder is named ``topic-dynamics`` (with a hyphen), which is not a
valid Python package name, so the importable code lives in the inner ``topics``
package and this thin wrapper runs it.

    cd topic-dynamics
    python run.py --max-papers 300
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from topics.pipeline import main  # noqa: E402

if __name__ == "__main__":
    main()
