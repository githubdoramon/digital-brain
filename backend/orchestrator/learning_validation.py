"""Typed validation details for local diagnosis and targeted model repairs."""

from observability.diagnostics import diagnostic


class LearningValidationError(ValueError):
    def __init__(self, code, reason, **details):
        self.code = code
        self.details = {"reason": reason, **details}
        super().__init__(code)


def reject(code, reason, **details):
    diagnostic("learning_validation_rejected", code=code, reason=reason, **details)
    raise LearningValidationError(code, reason, **details)
