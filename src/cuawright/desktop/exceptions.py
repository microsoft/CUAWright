class ReleaseError(RuntimeError):
    """Explicit release failure; messages must not contain external diagnostics."""
