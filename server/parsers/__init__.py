class ParsedRows(list):
    """A list of successfully-parsed transaction dicts that also carries any
    per-row parse errors encountered along the way (each a dict with at
    least a "reason" key). A malformed row is skipped and recorded here
    instead of aborting the whole file — callers that don't care about
    partial failures can keep treating this exactly like a plain list
    (equality/iteration/indexing all behave like list), while
    import_service.py surfaces .errors up to the API/UI."""

    def __init__(self, rows=()):
        super().__init__(rows)
        self.errors = []
