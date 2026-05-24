"""ImageGen domain exceptions."""


class ImageGenError(Exception):
    """Base for imagegen exceptions."""

    http_status = 500


class NoBackendAvailableError(ImageGenError, RuntimeError):
    http_status = 503
