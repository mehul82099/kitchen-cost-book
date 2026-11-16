#!/usr/bin/env python3
"""Tests for kitchen_cost_book. Run: python -m unittest -v"""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path

import kitchen_cost_book as kc


class MoneyTests(unittest.TestCase):
    def test_parse_basic(self):
        self.assertEqual(kc.parse_amount("1050.00"), 105000)

    def test_reject_three_decimals(self):
        with self.assertRaises(kc.CostBookError):
            kc.parse_amount("10.005")

    def test_reject_negative_and_zero(self):
        for bad in ("-5", "0"):
            with self.assertRaises(kc.CostBookError):
                kc.parse_amount(bad)

    def test_reject_text(self):
        with self.assertRaises(kc.CostBookError):
            kc.parse_amount("abc")

    def test_format_indian_grouping(self):
        self.assertEqual(kc.format_paise(12345678), "1,23,456.78")
        self.assertEqual(kc.format_paise(100), "1.00")

    def test_parse_qty_decimal(self):
        self.assertEqual(kc.parse_qty("2.5"), Decimal("2.5"))

    def test_parse_qty_rejects(self):
        for bad in ("0", "-1", "abc"):
            with self.assertRaises(kc.CostBookError):
                kc.parse_qty(bad)


class BookFixture(unittest.TestCase):
    def setUp(self):
        self.book = kc.CostBook()
        self.book.add_ingredient("Rice", "g")
        self.book.add_ingredient("Oil", "ml")


class IngredientTests(BookFixture):
    def test_add_and_lookup_by_name(self):
        ing = self.book.get_ingredient("rice")
        self.assertEqual(ing["id"], "i1")

    def test_duplicate_name_rejected_case_insensitive(self):
        with self.assertRaises(kc.CostBookError):
            self.book.add_ingredient("RICE", "g")

    def test_bad_unit_rejected(self):
        with self.assertRaises(kc.CostBookError):
            self.book.add_ingredient("Flour", "kg")  # base units only

    def test_empty_name_rejected(self):
        with self.assertRaises(kc.CostBookError):
            self.book.add_ingredient("  ", "g")

    def test_unknown_lookup(self):
        with self.assertRaises(kc.CostBookError):
            self.book.get_ingredient("i99")


class PriceTests(BookFixture):
    def test_unit_cost_math(self):
        e = self.book.add_price("i1", "Sharma Wholesale", "25000", "1050.00",
                                date(2026, 11, 1))
        self.assertEqual(kc.CostBook.unit_cost(e), Decimal("4.2"))

    def test_fractional_paise_kept(self):
        e = self.book.add_price("i2", "Oil Depot", "300", "1.00",
                                date(2026, 11, 1))  # 100/300 = 0.3333... p/ml
        self.assertEqual(kc.CostBook.unit_cost(e).quantize(Decimal("0.0001")),
                         Decimal("0.3333"))

    def test_blank_supplier_rejected(self):
        with self.assertRaises(kc.CostBookError):
            self.book.add_price("i1", "  ", "1000", "50.00", date(2026, 11, 1))

    def test_bad_qty_rejected(self):
        with self.assertRaises(kc.CostBookError):
            self.book.add_price("i1", "S", "0", "50.00", date(2026, 11, 1))

    def test_bad_date_rejected(self):
        with self.assertRaises(kc.CostBookError):
            kc.parse_date("01/11/2026")


class BestPriceTests(BookFixture):
    def setUp(self):
        super().setUp()
        # Rice: Sharma cheaper than Gupta on latest entries
        self.book.add_price("i1", "Gupta Traders", "25000", "1200.00", date(2026, 10, 1))
        self.book.add_price("i1", "Sharma Wholesale", "25000", "1050.00", date(2026, 10, 5))
        # Sharma's OLD cheaper price must not win over his latest
        self.book.add_price("i1", "Sharma Wholesale", "25000", "950.00", date(2026, 9, 1))

    def test_best_uses_latest_per_supplier(self):
        supplier, entry = self.book.best_unit_cost("i1")
        self.assertEqual(supplier, "Sharma Wholesale")
        self.assertEqual(entry["date"], "2026-10-05")
        self.assertEqual(kc.CostBook.unit_cost(entry), Decimal("4.2"))

    def test_supplier_latest_tie_goes_to_last_entry(self):
        self.book.add_price("i1", "Gupta Traders", "25000", "1100.00", date(2026, 10, 1))
        latest = self.book.latest_per_supplier("i1")
        self.assertEqual(latest["Gupta Traders"]["total_paise"], 110000)

    def test_latest_entry_overall(self):
        e = self.book.latest_entry("i1")
        self.assertEqual(e["date"], "2026-10-05")

    def test_no_prices_raises(self):
        with self.assertRaises(kc.CostBookError):
            self.book.best_unit_cost("i2")


class TrendTests(unittest.TestCase):
    def test_trend_math(self):
        book = kc.CostBook()
        book.add_ingredient("Onion", "g")
        book.add_price("i1", "A", "1000", "30.00", date(2026, 9, 1))   # 3.0 p/g
        book.add_price("i1", "A", "1000", "45.00", date(2026, 10, 1))  # 4.5 p/g
        book.add_price("i1", "B", "1000", "40.00", date(2026, 11, 1))  # 4.0 p/g
        t = book.trend("i1")
        self.assertEqual(t["count"], 3)
        self.assertEqual(t["first"], Decimal("3.0"))
        self.assertEqual(t["last"], Decimal("4.0"))
        self.assertEqual(t["min"], Decimal("3.0"))
        self.assertEqual(t["max"], Decimal("4.5"))
        self.assertEqual(t["change_pct"], Decimal("33.3"))

    def test_trend_no_prices(self):
        book = kc.CostBook()
        book.add_ingredient("Onion", "g")
        with self.assertRaises(kc.CostBookError):
            book.trend("i1")


class RecipeFixture(BookFixture):
    def setUp(self):
        super().setUp()
        self.book.add_price("i1", "Sharma Wholesale", "25000", "1050.00",
                            date(2026, 11, 1))  # 4.2 p/g
        self.book.add_price("i2", "Oil Depot", "5000", "400.00",
                            date(2026, 11, 1))  # 8.0 p/ml
        self.book.add_recipe("Veg Pulao")


class RecipeLineTests(RecipeFixture):
    def test_add_line(self):
        line = self.book.add_recipe_line("r1", "i1", "300")
        self.assertEqual(line["qty"], "300")

    def test_duplicate_ingredient_line_rejected(self):
        self.book.add_recipe_line("r1", "i1", "300")
        with self.assertRaises(kc.CostBookError):
            self.book.add_recipe_line("r1", "rice", "100")

    def test_remove_line(self):
        self.book.add_recipe_line("r1", "i1", "300")
        self.book.remove_recipe_line("r1", "i1")
        with self.assertRaises(kc.CostBookError):
            self.book.remove_recipe_line("r1", "i1")

    def test_recipe_name_lookup(self):
        self.assertEqual(self.book.get_recipe("veg pulao")["id"], "r1")


class CostingTests(RecipeFixture):
    def setUp(self):
        super().setUp()
        self.book.add_recipe_line("r1", "i1", "300")   # rice 300 g
        self.book.add_recipe_line("r1", "i2", "50")    # oil 50 ml

    def test_recipe_cost_best(self):
        c = self.book.recipe_cost("r1")
        # rice: 300 * 4.2 = 1260 paise; oil: 50 * 8.0 = 400 paise
        self.assertEqual(c["total"], 1660)
        self.assertEqual(c["lines"][0]["supplier"], "Sharma Wholesale")
        self.assertNotIn("selling_price", c)

    def test_cost_with_selling_price(self):
        self.book.set_selling_price("r1", "80.00")
        c = self.book.recipe_cost("r1")
        self.assertEqual(c["selling_price"], 8000)
        self.assertEqual(c["margin"], 6340)
        self.assertEqual(c["food_cost_pct"], Decimal("20.8"))

    def test_negative_margin_allowed(self):
        self.book.set_selling_price("r1", "10.00")
        c = self.book.recipe_cost("r1")
        self.assertEqual(c["margin"], 1000 - 1660)
        self.assertGreater(c["food_cost_pct"], 100)

    def test_line_rounding_half_up(self):
        book = kc.CostBook()
        book.add_ingredient("Spice", "g")
        book.add_price("i1", "S", "3", "0.10", date(2026, 11, 1))  # 3.333... p/g
        book.add_recipe("Tea")
        book.add_recipe_line("r1", "i1", "1")
        c = book.recipe_cost("r1")
        self.assertEqual(c["total"], 3)  # 3.333 -> 3

    def test_missing_price_blocks_cost(self):
        self.book.add_ingredient("Saffron", "g")
        self.book.add_recipe_line("r1", "i3", "1")
        with self.assertRaises(kc.CostBookError) as ctx:
            self.book.recipe_cost("r1")
        self.assertIn("Saffron", str(ctx.exception))

    def test_empty_recipe_rejected(self):
        self.book.add_recipe("Empty Dish")
        with self.assertRaises(kc.CostBookError):
            self.book.recipe_cost("r2")

    def test_latest_mode_prefers_recency_over_price(self):
        # fixture: Sharma latest is 4.2 p/g on 2026-11-01
        # Sharma's older cheaper entry must NOT win best mode (latest per supplier)
        self.book.add_price("i1", "Sharma Wholesale", "25000", "1000.00",
                            date(2026, 10, 1))   # 4.0 p/g, older -> ignored
        # Gupta's price is costlier but newer overall -> latest mode takes it
        self.book.add_price("i1", "Gupta Traders", "25000", "1100.00",
                            date(2026, 11, 10))  # 4.4 p/g, newest
        c_best = self.book.recipe_cost("r1", "best")
        c_latest = self.book.recipe_cost("r1", "latest")
        self.assertEqual(c_best["lines"][0]["line_cost"], 1260)   # 300*4.2
        self.assertEqual(c_latest["lines"][0]["line_cost"], 1320)  # 300*4.4

    def test_menu_cost_mixed(self):
        self.book.set_selling_price("r1", "80.00")
        self.book.add_recipe("Unpriced Dish")
        rows = self.book.menu_cost()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0][1])
        self.assertIsNone(rows[1][1])
        self.assertIn("no lines", rows[1][2])


class CsvTests(RecipeFixture):
    def test_export(self):
        self.book.add_recipe_line("r1", "i1", "300")
        self.book.add_recipe_line("r1", "i2", "50")
        self.book.set_selling_price("r1", "80.00")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "menu.csv"
            self.book.export_csv(out)
            with open(out, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["recipe", "cost", "selling_price", "margin",
                                   "food_cost_pct", "note"])
        self.assertEqual(rows[1][0], "Veg Pulao")
        self.assertEqual(rows[1][1], "16.60")
        self.assertEqual(rows[1][2], "80.00")
        self.assertEqual(rows[1][3], "63.40")
        self.assertEqual(rows[1][4], "20.8")


class PersistenceTests(RecipeFixture):
    def test_round_trip_and_lf_only(self):
        self.book.add_recipe_line("r1", "i1", "300")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            self.assertNotIn(b"\r\n", db.read_bytes())
            loaded = kc.CostBook.load(db)
        self.assertEqual(loaded.recipe_cost("r1")["total"], 1260)

    def test_failed_save_leaves_original_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "book.json"
            self.book.save(db)
            before = db.read_bytes()
            self.book.state["broken"] = object()
            try:
                with self.assertRaises(TypeError):
                    self.book.save(db)
                self.assertEqual(db.read_bytes(), before)
                leftovers = [p for p in Path(tmp).iterdir() if p.name != "book.json"]
                self.assertEqual(leftovers, [])
            finally:
                del self.book.state["broken"]

    def test_corrupt_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "bad.json"
            db.write_text("{oops", encoding="utf-8")
            with self.assertRaises(kc.CostBookError):
                kc.CostBook.load(db)

    def test_missing_file_gives_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = kc.CostBook.load(Path(tmp) / "none.json")
        self.assertEqual(loaded.summary()["ingredients"], 0)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "cli.json")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = kc.main(["--db", self.db] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_full_flow(self):
        self.assertEqual(self.run_cli("add-ingredient", "--name", "Rice", "--unit", "g")[0], 0)
        self.assertEqual(self.run_cli("add-ingredient", "--name", "Oil", "--unit", "ml")[0], 0)
        code, out, _ = self.run_cli("add-price", "--ingredient", "Rice",
                                    "--supplier", "Sharma Wholesale", "--qty", "25000",
                                    "--total", "1050.00", "--date", "2026-11-01")
        self.assertEqual(code, 0)
        self.assertIn("4.20 paise/g", out)
        self.run_cli("add-price", "--ingredient", "Oil", "--supplier", "Oil Depot",
                     "--qty", "5000", "--total", "400.00", "--date", "2026-11-01")
        code, out, _ = self.run_cli("best-price")
        self.assertIn("Sharma Wholesale", out)
        code, out, _ = self.run_cli("trend", "--ingredient", "Rice")
        self.assertIn("change", out)
        self.run_cli("add-recipe", "--name", "Veg Pulao")
        self.run_cli("add-line", "--recipe", "r1", "--ingredient", "Rice", "--qty", "300")
        self.run_cli("add-line", "--recipe", "r1", "--ingredient", "Oil", "--qty", "50")
        self.run_cli("set-price", "--recipe", "r1", "--sell", "80.00")
        code, out, _ = self.run_cli("cost", "--recipe", "r1")
        self.assertEqual(code, 0)
        self.assertIn("16.60", out)
        self.assertIn("63.40", out)
        self.assertIn("20.8%", out)
        code, out, _ = self.run_cli("menu-cost")
        self.assertIn("Veg Pulao", out)
        code, out, _ = self.run_cli("summary")
        self.assertIn("ingredients: 2", out)
        out_path = str(Path(self.tmp.name) / "menu.csv")
        code, _, _ = self.run_cli("export-csv", out_path)
        self.assertEqual(code, 0)
        with open(out_path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.reader(f))), 2)

    def test_error_exit_code(self):
        self.run_cli("add-ingredient", "--name", "Rice", "--unit", "g")
        code, _, err = self.run_cli("add-price", "--ingredient", "i99",
                                    "--supplier", "S", "--qty", "1", "--total", "1.00")
        self.assertEqual(code, 2)
        self.assertIn("no such ingredient", err)

    def test_prices_command(self):
        self.run_cli("add-ingredient", "--name", "Rice", "--unit", "g")
        self.run_cli("add-price", "--ingredient", "Rice", "--supplier", "A",
                     "--qty", "1000", "--total", "45.00", "--date", "2026-11-01")
        code, out, _ = self.run_cli("prices", "--ingredient", "Rice")
        self.assertEqual(code, 0)
        self.assertIn("45.00", out)
        self.assertIn("4.50 paise/g", out)

    def test_remove_line_cli(self):
        self.run_cli("add-ingredient", "--name", "Rice", "--unit", "g")
        self.run_cli("add-recipe", "--name", "Pulao")
        self.run_cli("add-line", "--recipe", "r1", "--ingredient", "Rice", "--qty", "300")
        code, _, _ = self.run_cli("remove-line", "--recipe", "r1", "--ingredient", "Rice")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
