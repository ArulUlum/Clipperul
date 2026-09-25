class PipelineError(Exception):
    """Raised by any pipeline stage. `code` is safe to show to users; the original
    exception/stack trace should only ever be logged server-side."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
