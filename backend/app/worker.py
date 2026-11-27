"""Worker process: python -m app.worker"""
import asyncio
import logging

from .scheduler import run_forever
from .seed import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    await init_db()
    await run_forever()


if __name__ == "__main__":
    asyncio.run(main())
