# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
import importlib.util
import sys
import tempfile
import os
import io
from contextlib import redirect_stdout

_ROOT = Path(__file__).resolve().parents[1]
_ASKMONEY_PATH = _ROOT / "sources" / "askmoney" / "askmoney_rub_thb.py"
_SPEC = importlib.util.spec_from_file_location("test_askmoney_rub_thb_mod", _ASKMONEY_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("askmoney_rub_thb")
am = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = am
_SPEC.loader.exec_module(am)


class TestAskMoneyRubThb(unittest.TestCase):
    def test_parse_params_falls_back_to_transfer_sheet_when_prefill_missing(self) -> None:
        html = '<html><script>var TRANSFER_SHEET_URL = "https://example.test/gviz";</script></html>'
        table = {
            "rows": [
                {"c": []},
                {"c": [None, None, None, None, None, {"v": "2.77"}]},
            ]
        }

        with patch.object(am, "load_transfer_table", return_value=table):
            params = am.parse_params_from_html(html)

        self.assertAlmostEqual(params.b2, 2.77)
        self.assertAlmostEqual(params.f2, am.DEFAULT_PARAMS["f2"])
        self.assertAlmostEqual(params.h2, am.DEFAULT_PARAMS["h2"])
        self.assertAlmostEqual(params.b4, am.DEFAULT_PARAMS["b4"])
        self.assertEqual(params.ladder, ())

    def test_rub_to_thb_uses_ladder_interpolation_and_floor_100(self) -> None:
        html = '<html><script>var TRANSFER_SHEET_URL = "https://example.test/gviz";</script></html>'

        def _mk_row(rub: str, thb: str):
            cells = [None] * 17
            cells[13] = {"v": rub}
            cells[16] = {"v": thb}
            return {"c": cells}

        table = {
            "rows": [
                {"c": []},
                {"c": [None, None, None, None, None, {"v": "2.5"}]},
                _mk_row("1000", "380"),
                _mk_row("2000", "760"),
            ]
        }

        with patch.object(am, "load_transfer_table", return_value=table):
            params = am.parse_params_from_html(html)

        # Внутри лестницы: линейная интерполяция 1500 -> 570 -> floor to 100 => 500
        self.assertEqual(am.rub_to_thb(1500.0, params), 500)
        # Вне лестницы: fallback по rate = b2 (20000 / 2.5 = 8000)
        self.assertEqual(am.rub_to_thb(20_000.0, params), 8000)

    def test_parse_params_raises_when_sheet_unavailable(self) -> None:
        html = "<html><body>no prefill and no transfer url</body></html>"

        with self.assertRaises(ValueError):
            am.parse_params_from_html(html)

    def test_parse_params_uses_embedded_rub_rate_when_sheet_fails(self) -> None:
        html = """
        <script>
        var TRANSFER_SHEET_URL = "https://example.test/gviz";
        var NON_CASH_RATES = {
          RUB: { code: "RUB", rate: 2.63 },
          USD: { code: "USD", rate: 31.17 }
        };
        </script>
        """
        with patch.object(am, "load_transfer_table", side_effect=RuntimeError("timeout")):
            params = am.parse_params_from_html(html)
        self.assertAlmostEqual(params.b2, 2.63)
        self.assertEqual(params.ladder, ())

    def test_cli_show_formula_html_file_does_not_require_params(self) -> None:
        html = (
            '&quot;variable&quot;:&quot;rub_bat_calc_out&quot;'
            '&quot;formula&quot;:&quot;floor((b11-800)/b2/100)*100&quot;,&quot;format&quot;'
        )
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(html)
            path = f.name
        try:
            with patch.object(am, "load_params", side_effect=AssertionError("load_params must not be called")):
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = am.cli_main(["--show-formula", "--html-file", path])
        finally:
            os.unlink(path)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
