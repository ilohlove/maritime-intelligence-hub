from app.logger import logger


ERROR_CLASSES = {
    "template": "Template error",
    "build": "Build error",
    "github": "GitHub error",
    "update": "Update error",
    "security": "Security error",
}


def log_classified_error(error_class, message, exc=None):
    label = ERROR_CLASSES.get(error_class, ERROR_CLASSES["template"])
    if exc is None:
        logger.error("%s: %s", label, message)
    else:
        logger.error("%s: %s | %s", label, message, exc)
