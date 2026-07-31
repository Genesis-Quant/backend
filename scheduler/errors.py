"""Scheduler integration exceptions."""


class DolphinSchedulerError(RuntimeError):
    """DolphinScheduler rejected an operation or could not be reached."""


class JobValidationError(ValueError):
    """A shared-directory job request is invalid."""


class JobStateError(RuntimeError):
    """A requested operation is invalid for the current job state."""
