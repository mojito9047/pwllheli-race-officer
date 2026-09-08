"""Core building blocks extracted from the monolithic app.py.

These modules hold pure, framework-independent logic (no Flask app, request
context or database access) so they can be imported and unit-tested in
isolation. app.py imports the public names back into its own namespace, so
existing `app.<name>` references and Jinja template helpers keep working
unchanged during the incremental refactor.
"""
