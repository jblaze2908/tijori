"""Service-level failures the API maps to status codes."""


class NotFound(LookupError):
    """Missing, or not visible to this member (the two are indistinguishable by design): 404."""


class Invalid(ValueError):
    """A well-formed request that cannot be applied: 422."""
