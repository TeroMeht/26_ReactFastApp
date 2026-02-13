import logging

def setup_logging(name: str = __name__) -> logging.Logger:
    """
    Setup global logging and return a logger object.
    
    - INFO level for your code
    - Suppress all logs from ib_async
    """
    # 1️⃣ Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # 2️⃣ Silence ib_async completely
    ib_logger = logging.getLogger("ib_async")
    ib_logger.addHandler(logging.NullHandler())  # discard messages
    ib_logger.propagate = False                  # don't bubble to root
    ib_logger.setLevel(logging.CRITICAL + 1)    # ignore all levels

    # 3️⃣ Return a logger for this module
    logger = logging.getLogger(name)
    return logger