# Analysis regression tests

Run `python -m pip install -r requirements.txt pytest`, then `python -m pytest`.
Tests use temporary SQLite databases and synthetic observations, never data/sales.db.
The legacy root run_ocr_test.py is a manual Windows PowerShell OCR utility, not
part of the automated suite; its fixed local screenshot paths are unavailable here.

Coverage: recent 8-week forecasts, zero observations, pending vs missing dates,
custom opening dates/closures, unavailable estimates, observed scenario bounds,
summary-only months, tenant isolation, factual component/day-average comparisons,
schedule validation/session protection, and authenticated page rendering.
