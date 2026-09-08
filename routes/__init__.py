"""HTTP route modules split out of app.py.

Each module registers a group of views on the shared Flask ``app`` under their
original endpoint names, with the original ``/x`` + ``/admin/x`` decorators.
app.py imports these modules at the end of its body (after all helpers/hooks) so
registration order and endpoint names are preserved. See routes/media.py for the
pattern.

Route modules must bind to the *running* app module via :func:`app_module`
rather than ``from app import ...``. When the app is started with ``python
app.py`` the running module is named ``__main__``; a bare ``from app import app``
would import app.py a *second* time as module ``app``, producing a separate
Flask instance whose routes are never served. :func:`app_module` returns the one
running instance whether app.py was launched directly or imported.
"""
import sys


def app_module():
    """Return the running app.py module (``app`` when imported, else ``__main__``)."""
    return sys.modules.get("app") or sys.modules["__main__"]
