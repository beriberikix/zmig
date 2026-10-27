"""Error types. Every ZmigError message is meant to be shown to the user as-is."""


class ZmigError(Exception):
    """A failure the user can act on. The CLI prints the message and exits 1."""


class ToolMissing(ZmigError):
    """A required external tool or Python package is not available."""


class ToolFailed(ZmigError):
    """An external command exited nonzero where success was required."""


class RefNotFound(ZmigError):
    """A --from/--to spec did not resolve to a commit."""


class ArtifactMissing(ZmigError):
    """A build artifact an analyzer needs was not produced."""
