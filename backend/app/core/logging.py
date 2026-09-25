import logging
import sys

# These are extremely chatty at DEBUG (full request/response dumps including headers) and
# drown out application logs — keep them quiet regardless of the app's own log level.
_NOISY_THIRD_PARTY_LOGGERS = ("boto3", "botocore", "urllib3", "s3transfer", "faster_whisper")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
