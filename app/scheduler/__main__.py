import asyncio
import logging

from app.scheduler.loop import main

logging.basicConfig(level=logging.INFO)
asyncio.run(main())
