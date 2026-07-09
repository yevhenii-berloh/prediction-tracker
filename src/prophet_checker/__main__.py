import logging

import uvicorn

from prophet_checker.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "prophet_checker.app:app",
        host=settings.app_host,
        port=8000,
        log_level=settings.log_level.lower(),
    )
