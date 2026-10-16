"""French-only string table with a tr() lookup.

Adapted from dnd_logger@bc2bdf1 src/i18n/__init__.py: the app is French-only, so the
English base and the live language switching are gone, but tr() is kept so that the
vendored modules can stay untouched.
"""

from .fr import STRINGS


def tr(key: str, **kwargs) -> str:
    """Look up a string key, format it with kwargs, and fall back to the key itself.

    Args:
        key: Dotted string key, e.g. ``"session.btn.record"``.
        **kwargs: Values substituted into ``{placeholders}``.

    Returns:
        The French string, or the key when it is missing.
    """
    text = STRINGS.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text
