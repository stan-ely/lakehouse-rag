"""Task entry point for the Databricks job; the work is the same code the container runs.

A serverless `spark_python_task` puts the script's own directory on `sys.path`, not the bundle
root, so `import ingestion.spark` would fail. This adds the bundle root and then hands over to
`ingestion.spark.run`, which is the module `mise run ingest` executes locally. Keeping the
entry thin is the point: the Databricks path must not become a second implementation.
"""

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
if str(BUNDLE_ROOT) not in sys.path:
    sys.path.insert(0, str(BUNDLE_ROOT))

from ingestion.spark.run import main  # noqa: E402 - must follow the sys.path bootstrap

if __name__ == "__main__":
    main(sys.argv[1:])
