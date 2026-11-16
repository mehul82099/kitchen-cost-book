#!/usr/bin/env python3
"""kitchen-cost-book: zero-dependency ingredient price book and recipe costing
CLI for small restaurants and home kitchens.

Track what each supplier actually charged you for an ingredient, see who is
cheapest right now and how prices moved, and cost a dish against its selling
price. Manual entry, offline, one JSON file - no feeds, no accounts.

Python 3.9+, standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

BASE_UNITS = ("g", "ml", "piece")


class CostBookError(Exception):
    """User-facing error: bad input, unknown entity, invalid state."""


# ---------------------------------------------------------------------------
# money / numbers
# ---------------------------------------------------------------------------

def parse_amount(text):
    """Parse a rupee amount string into integer paise. Strict: max 2 decimals."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise CostBookError("invalid amount: %r" % text)
    if not value.is_finite():
        raise CostBookError("invalid amount: %r" % text)
    if value != value.quantize(Decimal("0.01")):
        raise CostBookError("amounts support at most 2 decimal places: %r" % text)
    if value <= 0:
        raise CostBookError("amount must be positive: %r" % text)
    return int(value * 100)


def parse_qty(text):
    """Parse a quantity in base units (allows decimals like 2.5)."""
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise CostBookError("invalid quantity: %r" % text)
    if not value.is_finite() or value <= 0:
        raise CostBookError("quantity must be positive: %r" % text)
    return value


def format_paise(paise):
    """Format integer paise as rupees with Indian digit grouping (1,23,456.78)."""
    neg = paise < 0
    rupees, rem = divmod(abs(int(paise)), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while head:
            groups.append(head[-2:])
            head = head[:-2]
        s = ",".join(reversed(groups)) + "," + tail
    return ("-" if neg else "") + s + ".%02d" % rem


def format_unit_cost(paise_per_unit):
    """Format a Decimal paise-per-base-unit cost readably (paise with 2dp)."""
    return "%s paise" % paise_per_unit.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_date(text):
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        raise CostBookError("invalid date %r (expected YYYY-MM-DD)" % text)


def round_paise(decimal_paise):
    return int(decimal_paise.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# cost book
# ---------------------------------------------------------------------------

class CostBook:
    def __init__(self, state=None):
        self.state = state or {
            "ingredients": [],
            "prices": [],
            "recipes": [],
            "next": {"ingredient": 1, "recipe": 1},
        }

    @classmethod
    def load(cls, path):
        p = Path(path)
        if p.exists():
            try:
                return cls(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError as e:
                raise CostBookError("corrupt store %s: %s" % (p, e))
        return cls()

    def save(self, path):
        # Serialize fully, write a temp file, then atomically replace, so a
        # failed save leaves the previous store untouched. newline="" keeps
        # bytes identical on Windows and Linux.
        payload = json.dumps(self.state, indent=2, sort_keys=True) + "\n"
        path = Path(path)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(payload)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- ingredients -------------------------------------------------------

    def add_ingredient(self, name, unit):
        name = (name or "").strip()
        if not name:
            raise CostBookError("ingredient name is required")
        if unit not in BASE_UNITS:
            raise CostBookError("unit must be one of %s (base units)" % (BASE_UNITS,))
        for ing in self.state["ingredients"]:
            if ing["name"].lower() == name.lower():
                raise CostBookError("ingredient already exists: %s" % ing["name"])
        iid = "i%d" % self.state["next"]["ingredient"]
        self.state["next"]["ingredient"] += 1
        ing = {"id": iid, "name": name, "unit": unit}
        self.state["ingredients"].append(ing)
        return ing

    def get_ingredient(self, ref):
        for ing in self.state["ingredients"]:
            if ing["id"] == ref or ing["name"].lower() == str(ref).lower():
                return ing
        raise CostBookError("no such ingredient: %s" % ref)

    # -- prices --------------------------------------------------------------

    def add_price(self, ingredient_ref, supplier, qty_text, total_text, entry_date):
        ing = self.get_ingredient(ingredient_ref)
        supplier = (supplier or "").strip()
        if not supplier:
            raise CostBookError("supplier name is required")
        qty = parse_qty(qty_text)  # in the ingredient's base unit
        total_paise = parse_amount(total_text)
        entry = {
            "ingredient_id": ing["id"],
            "supplier": supplier,
            "date": entry_date.isoformat(),
            "qty": str(qty),
            "total_paise": total_paise,
        }
        self.state["prices"].append(entry)
        return entry

    def unit_cost(entry):
        """Decimal paise per base unit for one price entry."""
        return Decimal(entry["total_paise"]) / Decimal(entry["qty"])

    unit_cost = staticmethod(unit_cost)

    def _entries(self, ingredient_id):
        return [p for p in self.state["prices"] if p["ingredient_id"] == ingredient_id]

    def latest_per_supplier(self, ingredient_id):
        """Most recent entry per supplier (by date, then entry order)."""
        latest = {}
        for entry in self._entries(ingredient_id):
            cur = latest.get(entry["supplier"])
            if cur is None or (entry["date"], entry.get("_seq", 0)) >= (
                cur["date"], cur.get("_seq", 0)
            ):
                latest[entry["supplier"]] = entry
        return latest

    def best_unit_cost(self, ingredient_id):
        """(supplier, entry) with the lowest unit cost among latest-per-supplier."""
        latest = self.latest_per_supplier(ingredient_id)
        if not latest:
            raise CostBookError("no prices recorded for this ingredient yet")
        supplier = min(latest, key=lambda s: self.unit_cost(latest[s]))
        return supplier, latest[supplier]

    def latest_entry(self, ingredient_id):
        entries = self._entries(ingredient_id)
        if not entries:
            raise CostBookError("no prices recorded for this ingredient yet")
        return max(entries, key=lambda e: (e["date"], self.state["prices"].index(e)))

    def trend(self, ingredient_id):
        entries = sorted(self._entries(ingredient_id), key=lambda e: e["date"])
        if not entries:
            raise CostBookError("no prices recorded for this ingredient yet")
        costs = [self.unit_cost(e) for e in entries]
        first, last = costs[0], costs[-1]
        change = ((last - first) / first * 100) if first else Decimal("0")
        return {
            "count": len(entries),
            "first_date": entries[0]["date"],
            "last_date": entries[-1]["date"],
            "first": first,
            "last": last,
            "min": min(costs),
            "max": max(costs),
            "change_pct": change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        }

    # -- recipes -------------------------------------------------------------

    def add_recipe(self, name):
        name = (name or "").strip()
        if not name:
            raise CostBookError("recipe name is required")
        for r in self.state["recipes"]:
            if r["name"].lower() == name.lower():
                raise CostBookError("recipe already exists: %s" % r["name"])
        rid = "r%d" % self.state["next"]["recipe"]
        self.state["next"]["recipe"] += 1
        recipe = {"id": rid, "name": name, "lines": [], "selling_price_paise": None}
        self.state["recipes"].append(recipe)
        return recipe

    def get_recipe(self, ref):
        for r in self.state["recipes"]:
            if r["id"] == ref or r["name"].lower() == str(ref).lower():
                return r
        raise CostBookError("no such recipe: %s" % ref)

    def add_recipe_line(self, recipe_ref, ingredient_ref, qty_text):
        recipe = self.get_recipe(recipe_ref)
        ing = self.get_ingredient(ingredient_ref)
        qty = parse_qty(qty_text)
        for line in recipe["lines"]:
            if line["ingredient_id"] == ing["id"]:
                raise CostBookError(
                    "%s is already a line in %s; remove it first to change the qty"
                    % (ing["name"], recipe["name"])
                )
        line = {"ingredient_id": ing["id"], "qty": str(qty)}
        recipe["lines"].append(line)
        return line

    def remove_recipe_line(self, recipe_ref, ingredient_ref):
        recipe = self.get_recipe(recipe_ref)
        ing = self.get_ingredient(ingredient_ref)
        before = len(recipe["lines"])
        recipe["lines"] = [
            l for l in recipe["lines"] if l["ingredient_id"] != ing["id"]
        ]
        if len(recipe["lines"]) == before:
            raise CostBookError("%s is not a line in %s" % (ing["name"], recipe["name"]))

    def set_selling_price(self, recipe_ref, amount_text):
        recipe = self.get_recipe(recipe_ref)
        recipe["selling_price_paise"] = parse_amount(amount_text)
        return recipe["selling_price_paise"]

    # -- costing -------------------------------------------------------------

    def _pick_entry(self, ingredient_id, mode):
        if mode == "best":
            return self.best_unit_cost(ingredient_id)
        entry = self.latest_entry(ingredient_id)
        return entry["supplier"], entry

    def recipe_cost(self, recipe_ref, mode="best"):
        if mode not in ("best", "latest"):
            raise CostBookError("price mode must be 'best' or 'latest'")
        recipe = self.get_recipe(recipe_ref)
        if not recipe["lines"]:
            raise CostBookError("recipe %s has no lines" % recipe["name"])
        missing = []
        lines = []
        for line in recipe["lines"]:
            ing = self.get_ingredient(line["ingredient_id"])
            if not self._entries(ing["id"]):
                missing.append(ing["name"])
                continue
            supplier, entry = self._pick_entry(ing["id"], mode)
            unit_cost = self.unit_cost(entry)
            line_cost = round_paise(Decimal(line["qty"]) * unit_cost)
            lines.append(
                {
                    "ingredient": ing["name"],
                    "unit": ing["unit"],
                    "qty": Decimal(line["qty"]),
                    "supplier": supplier,
                    "unit_cost": unit_cost,
                    "line_cost": line_cost,
                }
            )
        if missing:
            raise CostBookError(
                "no prices recorded for: %s (add prices before costing)"
                % ", ".join(missing)
            )
        total = sum(l["line_cost"] for l in lines)
        result = {"recipe": recipe["name"], "mode": mode, "lines": lines, "total": total}
        sell = recipe["selling_price_paise"]
        if sell is not None:
            result["selling_price"] = sell
            result["margin"] = sell - total
            result["food_cost_pct"] = (
                Decimal(total) / sell * 100
            ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return result

    def menu_cost(self, mode="best"):
        out = []
        for recipe in self.state["recipes"]:
            try:
                out.append((recipe, self.recipe_cost(recipe["id"], mode), None))
            except CostBookError as e:
                out.append((recipe, None, str(e)))
        return out

    def export_csv(self, path, mode="best"):
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["recipe", "cost", "selling_price", "margin", "food_cost_pct", "note"]
            )
            for recipe, cost, err in self.menu_cost(mode):
                if err:
                    w.writerow([recipe["name"], "", "", "", "", err])
                else:
                    w.writerow(
                        [
                            recipe["name"],
                            "%.2f" % (cost["total"] / 100),
                            "%.2f" % (cost.get("selling_price", 0) / 100)
                            if cost.get("selling_price") is not None
                            else "",
                            "%.2f" % (cost["margin"] / 100)
                            if "margin" in cost
                            else "",
                            str(cost.get("food_cost_pct", "")),
                            "",
                        ]
                    )

    def summary(self):
        suppliers = {p["supplier"] for p in self.state["prices"]}
        return {
            "ingredients": len(self.state["ingredients"]),
            "price_entries": len(self.state["prices"]),
            "suppliers": len(suppliers),
            "recipes": len(self.state["recipes"]),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="kitchen-cost-book",
        description="Ingredient price book and recipe costing for small kitchens.",
    )
    p.add_argument("--db", default="kitchen-cost-book.json", help="store file path")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("add-ingredient", help="add an ingredient with its base unit")
    sp.add_argument("--name", required=True)
    sp.add_argument("--unit", required=True, choices=BASE_UNITS,
                    help="base unit for all entries: g, ml or piece")

    sp = sub.add_parser("add-price", help="record what a supplier charged")
    sp.add_argument("--ingredient", required=True, help="id or name")
    sp.add_argument("--supplier", required=True)
    sp.add_argument("--qty", required=True, help="quantity in base units, e.g. 25000 (g)")
    sp.add_argument("--total", required=True, help="total paid in rupees, e.g. 1050.00")
    sp.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")

    sp = sub.add_parser("prices", help="price history for an ingredient")
    sp.add_argument("--ingredient", required=True)

    sp = sub.add_parser("best-price", help="cheapest current supplier per ingredient")

    sp = sub.add_parser("trend", help="price movement for an ingredient")
    sp.add_argument("--ingredient", required=True)

    sp = sub.add_parser("add-recipe", help="add a recipe/dish")
    sp.add_argument("--name", required=True)

    sp = sub.add_parser("add-line", help="add an ingredient line to a recipe")
    sp.add_argument("--recipe", required=True)
    sp.add_argument("--ingredient", required=True)
    sp.add_argument("--qty", required=True, help="quantity in the ingredient's base unit")

    sp = sub.add_parser("remove-line", help="remove an ingredient line from a recipe")
    sp.add_argument("--recipe", required=True)
    sp.add_argument("--ingredient", required=True)

    sp = sub.add_parser("set-price", help="set the dish selling price")
    sp.add_argument("--recipe", required=True)
    sp.add_argument("--sell", required=True, help="selling price in rupees")

    sp = sub.add_parser("cost", help="cost one recipe")
    sp.add_argument("--recipe", required=True)
    sp.add_argument("--price-mode", choices=["best", "latest"], default="best",
                    help="best: cheapest current supplier; latest: most recent entry")

    sp = sub.add_parser("menu-cost", help="cost every recipe")
    sp.add_argument("--price-mode", choices=["best", "latest"], default="best")

    sp = sub.add_parser("export-csv", help="export recipe costing to CSV")
    sp.add_argument("path")
    sp.add_argument("--price-mode", choices=["best", "latest"], default="best")

    sp = sub.add_parser("summary", help="counts at a glance")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        book = CostBook.load(args.db)
        mutate = True

        if args.cmd == "add-ingredient":
            ing = book.add_ingredient(args.name, args.unit)
            out = "added ingredient %s: %s (%s)" % (ing["id"], ing["name"], ing["unit"])
        elif args.cmd == "add-price":
            d = parse_date(args.date) if args.date else date.today()
            e = book.add_price(args.ingredient, args.supplier, args.qty, args.total, d)
            ing = book.get_ingredient(args.ingredient)
            out = "recorded: %s from %s = %s/%s on %s" % (
                ing["name"], e["supplier"],
                format_unit_cost(CostBook.unit_cost(e)), ing["unit"], e["date"])
        elif args.cmd == "prices":
            mutate = False
            ing = book.get_ingredient(args.ingredient)
            entries = sorted(book._entries(ing["id"]), key=lambda e: e["date"])
            lines = ["%s (%s) price history:" % (ing["name"], ing["unit"])]
            for e in entries:
                lines.append("  %s  %-20s %s for %s %s  (%s/%s)" % (
                    e["date"], e["supplier"], format_paise(e["total_paise"]),
                    e["qty"], ing["unit"], format_unit_cost(CostBook.unit_cost(e)),
                    ing["unit"]))
            out = "\n".join(lines) if entries else "no prices for %s yet" % ing["name"]
        elif args.cmd == "best-price":
            mutate = False
            lines = []
            for ing in book.state["ingredients"]:
                try:
                    supplier, e = book.best_unit_cost(ing["id"])
                    lines.append("%-20s %-20s %s/%s (as of %s)" % (
                        ing["name"], supplier, format_unit_cost(book.unit_cost(e)),
                        ing["unit"], e["date"]))
                except CostBookError:
                    lines.append("%-20s (no prices yet)" % ing["name"])
            out = "\n".join(lines) if lines else "no ingredients yet"
        elif args.cmd == "trend":
            mutate = False
            ing = book.get_ingredient(args.ingredient)
            t = book.trend(ing["id"])
            out = "\n".join([
                "%s: %d price entries (%s to %s)" % (
                    ing["name"], t["count"], t["first_date"], t["last_date"]),
                "  first: %s/%s   latest: %s/%s   change: %s%%" % (
                    format_unit_cost(t["first"]), ing["unit"],
                    format_unit_cost(t["last"]), ing["unit"], t["change_pct"]),
                "  min: %s/%s   max: %s/%s" % (
                    format_unit_cost(t["min"]), ing["unit"],
                    format_unit_cost(t["max"]), ing["unit"]),
            ])
        elif args.cmd == "add-recipe":
            r = book.add_recipe(args.name)
            out = "added recipe %s: %s" % (r["id"], r["name"])
        elif args.cmd == "add-line":
            book.add_recipe_line(args.recipe, args.ingredient, args.qty)
            out = "added line to %s" % args.recipe
        elif args.cmd == "remove-line":
            book.remove_recipe_line(args.recipe, args.ingredient)
            out = "removed line from %s" % args.recipe
        elif args.cmd == "set-price":
            price = book.set_selling_price(args.recipe, args.sell)
            out = "selling price set: %s" % format_paise(price)
        elif args.cmd == "cost":
            mutate = False
            c = book.recipe_cost(args.recipe, args.price_mode)
            lines = ["%s (price mode: %s)" % (c["recipe"], c["mode"])]
            for l in c["lines"]:
                lines.append("  %-20s %8s %-5s @ %-14s %-16s = %s" % (
                    l["ingredient"], l["qty"], l["unit"],
                    format_unit_cost(l["unit_cost"]) + "/" + l["unit"],
                    "(" + l["supplier"] + ")", format_paise(l["line_cost"])))
            lines.append("  total dish cost: %s" % format_paise(c["total"]))
            if "selling_price" in c:
                lines.append("  selling price  : %s" % format_paise(c["selling_price"]))
                lines.append("  margin         : %s  (food cost %s%%)" % (
                    format_paise(c["margin"]), c["food_cost_pct"]))
            out = "\n".join(lines)
        elif args.cmd == "menu-cost":
            mutate = False
            lines = []
            for recipe, cost, err in book.menu_cost(args.price_mode):
                if err:
                    lines.append("%-24s %s" % (recipe["name"], err))
                elif "selling_price" in cost:
                    lines.append("%-24s cost %-10s sell %-10s margin %-10s (food cost %s%%)" % (
                        recipe["name"], format_paise(cost["total"]),
                        format_paise(cost["selling_price"]),
                        format_paise(cost["margin"]), cost["food_cost_pct"]))
                else:
                    lines.append("%-24s cost %s (no selling price set)" % (
                        recipe["name"], format_paise(cost["total"])))
            out = "\n".join(lines) if lines else "no recipes yet"
        elif args.cmd == "export-csv":
            mutate = False
            book.export_csv(args.path, args.price_mode)
            out = "wrote %s" % args.path
        elif args.cmd == "summary":
            mutate = False
            s = book.summary()
            out = "ingredients: %d   price entries: %d   suppliers: %d   recipes: %d" % (
                s["ingredients"], s["price_entries"], s["suppliers"], s["recipes"])
        else:  # pragma: no cover - argparse enforces choices
            raise CostBookError("unknown command")

        if mutate:
            book.save(args.db)
        print(out)
        return 0
    except CostBookError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
