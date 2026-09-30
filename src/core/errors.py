"""Typed error hierarchy. Nothing internal leaks across the transport boundary."""

from __future__ import annotations


class RantiError(Exception):
    """Base class for every error this application raises on purpose."""

    code = "ranti_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(RantiError):
    code = "configuration_error"


class NotFoundError(RantiError):
    code = "not_found"


class ConflictError(RantiError):
    code = "conflict"


class ValidationError(RantiError):
    code = "validation_error"


class DependencyUnavailableError(RantiError):
    """An outbound dependency failed and no fallback could satisfy the call."""

    code = "dependency_unavailable"

    def __init__(self, dependency: str, message: str) -> None:
        super().__init__(message)
        self.dependency = dependency


class DependencyTimeoutError(DependencyUnavailableError):
    code = "dependency_timeout"


class CircuitOpenError(DependencyUnavailableError):
    code = "circuit_open"
