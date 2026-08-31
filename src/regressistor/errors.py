"""Public exception hierarchy."""


class RegressistorError(Exception):
    """Base class for expected user-facing failures."""


class InputError(RegressistorError):
    """Raised when a policy, bundle, or report is malformed."""


class UnitError(InputError):
    """Raised when a unit is unknown or dimensionally incompatible."""


class OutputError(RegressistorError):
    """Raised when an output cannot be written safely."""
