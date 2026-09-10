"""Unit tests for the Job Application Suite.

Run from the project root:

    python -m unittest discover -s tests -v

Tests are hermetic: the SQLite database, CV profile and AI cache are redirected to
temporary paths, so no real data is touched.
"""
