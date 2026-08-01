"""Completeness-qualified history envelope and shadow adapter.

This package is deliberately outside ``core`` and ``app``.  Nothing in the
served application imports it, it exposes no route, and it never becomes an
input to Today ranking, badges, or notifications.  Its only consumers are the
standalone shadow harness (``python3 -m history.shadow``) and the test suite.

The package is installation-generic.  Every deployment-specific value — home
path, time zone, source requirements, mirror declarations, store location —
arrives through an explicit runtime settings document that is never committed
to this repository.
"""
