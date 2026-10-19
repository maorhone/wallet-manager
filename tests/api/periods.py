from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.spend import previous_period

CURRENT = datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m")
PREVIOUS = previous_period(CURRENT)
