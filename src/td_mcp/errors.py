"""Errors the server raises. Each message states the next step."""


class BridgeError(Exception):
    """The TouchDesigner bridge could not be reached or refused the request."""


class DocsError(Exception):
    """The offline documentation could not be read or searched."""
