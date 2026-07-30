from my_logging.logger import setup_logging
# Set up logging and get a logger
logger = setup_logging(__name__)
logger.info("Application backend starting")

from core.app_factory import create_app
import uvicorn

# Uvicorn imports this module by name ("main:app") to find the ASGI
# application. All construction lives in core.app_factory; all lifecycle
# logic lives in core.lifespan + core.startup.*.
app = create_app()


if __name__ == "__main__":
    uvicorn.run("main:app")
