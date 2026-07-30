# Changelog

## Unreleased - 2026-07-30

- Fix: import API_BASE in `custom_components/fireboard/api.py` to avoid NameError when calling session/device endpoints.
- Tests: add unit tests for URL construction in FireboardClient (tests/test_api.py).
