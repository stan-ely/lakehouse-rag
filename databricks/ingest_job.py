"""Task entry point for the Databricks job; the work is the same code the container runs.

`ingestion` is installed from the bundle's wheel (databricks/pyproject.toml) on the driver and
the executors alike, so this only hands over to `ingestion.spark.run`, the module
`mise run ingest` executes locally. Keeping the entry thin is the point: the Databricks path
must not become a second implementation.
"""

import sys

from ingestion.spark.run import main

if __name__ == "__main__":
    main(sys.argv[1:])
