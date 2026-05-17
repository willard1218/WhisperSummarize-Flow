import time
import functools
import random
from tools.logger import get_logger

logger = get_logger(__name__)

def retry(max_retries=3, initial_delay=1, backoff_factor=2, exceptions=(Exception,)):
    """
    Retry decorator with exponential backoff and jitter.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            delay = initial_delay
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retries += 1
                    if retries >= max_retries:
                        logger.error(f"Function {func.__name__} failed after {max_retries} attempts: {e}")
                        raise
                    
                    # Exponential backoff with jitter
                    jitter = random.uniform(0, 0.1 * delay)
                    sleep_time = delay + jitter
                    logger.warning(f"Function {func.__name__} failed (attempt {retries}/{max_retries}): {e}. Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator
