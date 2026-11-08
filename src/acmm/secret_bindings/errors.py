class secret_binding_error(ValueError):
    """Raised when a local ACMM secret binding cannot be resolved safely."""


class secret_binding_version_error(secret_binding_error):
    """Raised when a registry schema version is valid but incompatible with Core."""


__all__ = ["secret_binding_error", "secret_binding_version_error"]
