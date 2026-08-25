"""Import this for its side effect: the engine stops reading operator data.

`royalty_overrides.json` is what the operator types into the app's Product
Royalty tab, and products.get_econ consults it for every product type in every
market. A test that reads the real file passes or fails depending on what was
typed that morning — on 2026-08-20 three product types entered by their
dashboard label ("Basketball Jersey") turned two export-reader tests red without
a line of code changing.

So any test module that touches economics imports this first. It points the
overlay at a path that does not exist, leaving the shipped tables as the only
truth. Modules that are ABOUT the overlay point CONFIG at their own temp file
and restore it afterwards.

It lives in its own module rather than in tests/__init__.py because
`unittest discover` imports test modules TOP-LEVEL (as `export_reader_tests`,
not `tests.export_reader_tests`), so the package __init__ never runs.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "engine"))

import royalty_config  # noqa: E402

royalty_config.CONFIG = os.path.join(_HERE, "_no_operator_overrides.json")
royalty_config.invalidate()
