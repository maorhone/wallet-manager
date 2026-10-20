from pydantic import RootModel


class PrefsIn(RootModel[dict[str, bool]]):
    """``{"<kind>": muted}``; the service rejects unknown kinds and muting a critical kind."""
