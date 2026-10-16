"""python -m trading.runtime - Paper-Dienst (AP-27). Standardmaessig AUS; siehe trading.runtime.service."""

import os
import sys

from trading.ops.logs import configure_logging
from trading.runtime.service import main

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))  # JSON-Zeilen, Geheimnisse geschwaerzt (AP-25)
sys.exit(main())
