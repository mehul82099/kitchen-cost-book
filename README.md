# kitchen-cost-book

A zero-dependency command-line price book and recipe costing tool for small
restaurants. Record what each supplier actually charged you per ingredient,
and the tool answers: who is cheapest right now, how prices are moving, and
what each dish on the menu really costs against its selling price.

Single Python file, standard library only (Python 3.9+). Works fully offline;
all data lives in one local JSON file you own.

## Why this exists

On Razorpay's public "Fix My Itch" list, one entry asks: "Why can't small
restaurants access wholesale ingredient pricing without large minimum
orders?" (page fetched and entry re-verified on 2026-11-16;
https://razorpay.com/m/fix-my-itch/). That list is curated by Razorpay - it
shows the problem was submitted and published, not how common it is.

This tool does not get anyone wholesale prices. It tackles the follow-on
problem a small kitchen *can* control: knowing what you actually paid, per
supplier, per gram/ml/piece, over time - so you can see which supplier is
really cheapest for each ingredient, catch price creep, and check whether a
dish's menu price still covers its ingredient cost after the latest increase.

## What it does

- **Ingredient price book** - track each ingredient in a base unit (g, ml,
  or piece) and log price quotes/purchases per supplier with a date.
- **Best price per ingredient** - compares each supplier's *latest* price on
  a normalized paise-per-base-unit basis, so "Rs 1,050 for 25 kg" and
  "Rs 45 for 1 kg" compare fairly.
- **Price trend** - first, last, min, max unit price and the % change over
  your recorded history.
- **Recipe costing** - cost a dish from its ingredient quantities, at the
  best current price or the latest recorded price. Set a selling price to
  see margin and food-cost percentage.
- **Menu cost CSV** - export the whole menu's cost/margin table for a
  spreadsheet.

## What it does NOT do (limits)

- **Manual entry only.** No live price feeds, no supplier scraping, no
  marketplace. It records prices *you* type in.
- **Not inventory.** It tracks prices and recipe math, not stock on hand,
  depletion, wastage, or reorder points.
- **Not purchasing.** No purchase orders, invoices, GST handling, or
  supplier payments.
- **Unit discipline is on you.** Costs are only comparable if you always
  record the same ingredient in the same base unit (e.g. always grams for
  rice). Entering "per 1 kg" for one supplier and "per 500 g" for another is
  fine - the total and quantity you enter are what get normalized - but
  mixing grams and kilograms *as the base unit of two different ingredient
  entries* for the same food will give nonsense.
- **Durability:** saves are atomic (write to a temp file, then replace the
  store), and a failed save leaves the previous store intact - that behavior
  is covered by tests. Beyond that there are no guarantees against power
  loss mid-write or two people editing the same file at once.

## Landscape note (checked 2026-11-16)

Dated searches on 2026-11-16 for `recipe cost calculator cli` and
`ingredient price tracker restaurant` surfaced recipe-costing tools as web
or mobile apps (e.g. hosted calculators and a Flutter app template). This
project is a single-file offline CLI instead. That is a statement about what
those searches found on that date, not a claim that no other CLI exists.

## Install and quick start

Requires Python 3.9+. No dependencies.

```bash
python3 kitchen_cost_book.py --help
```

Walkthrough (each command saves automatically to `--db`, default
`kitchen_cost_book.json`):

```bash
# ingredients, each pinned to a base unit
python3 kitchen_cost_book.py add-ingredient --name Rice --unit g
python3 kitchen_cost_book.py add-ingredient --name "Sunflower Oil" --unit ml

# record what suppliers charged (total price for that pack quantity)
python3 kitchen_cost_book.py add-price --ingredient Rice \
  --supplier "Sharma Wholesale" --qty 25000 --total 1050.00 --date 2026-11-01
python3 kitchen_cost_book.py add-price --ingredient Rice \
  --supplier "Gupta Traders" --qty 25000 --total 1200.00 --date 2026-11-01
python3 kitchen_cost_book.py add-price --ingredient "Sunflower Oil" \
  --supplier "Oil Depot" --qty 5000 --total 400.00 --date 2026-11-01

# who is cheapest now, and how is rice trending?
python3 kitchen_cost_book.py best-price
python3 kitchen_cost_book.py trend --ingredient Rice

# cost a dish
python3 kitchen_cost_book.py add-recipe --name "Veg Pulao"
python3 kitchen_cost_book.py add-line --recipe r1 --ingredient Rice --qty 300
python3 kitchen_cost_book.py add-line --recipe r1 --ingredient "Sunflower Oil" --qty 50
python3 kitchen_cost_book.py set-price --recipe r1 --sell 80.00
python3 kitchen_cost_book.py cost --recipe r1
python3 kitchen_cost_book.py menu-cost            # whole menu table
python3 kitchen_cost_book.py export-csv menu.csv  # same, as CSV
```

Example `cost --recipe r1` output:

```
Veg Pulao (price mode: best)
  Rice                      300 g     @ 4.20 paise/g   (Sharma Wholesale) = 12.60
  Sunflower Oil              50 ml    @ 8.00 paise/ml  (Oil Depot)      = 4.00
  total dish cost: 16.60
  selling price  : 80.00
  margin         : 63.40  (food cost 20.8%)
```

Ingredients and recipes can be referred to by id (`i1`, `r1`) or by exact
name. `remove-line --recipe r1 --ingredient Rice` removes a line;
`prices --ingredient Rice` lists the raw price history.

## How money and quantities are handled

- Prices are stored in integer paise (no float drift); `1050.00` is rejected
  if it has more than 2 decimal places, and non-positive amounts are
  rejected.
- Unit cost is computed in Decimal arithmetic as `paise / quantity` and can
  be fractional (e.g. 4.2 paise per gram); a recipe line's cost is rounded
  to the nearest paise (half up).
- Food-cost % is `ingredient cost / selling price x 100`, rounded to one
  decimal. A margin can be negative - the tool shows it rather than hiding
  it.
- Quantities are Decimal, so `2.5` kg-style entries work; zero and negative
  quantities are rejected.
- `--price-mode latest` (on `cost` and `menu-cost`) prices every ingredient
  at its most recent recorded entry instead of the cheapest supplier's
  latest - useful to see "what would this dish cost if I bought today".

## Data and tests

- Store: one JSON file (`--db`, default `kitchen_cost_book.json` next to
  where you run it), written with LF line endings and atomic replace. A
  failed serialization leaves the previous file untouched (tested).
- Tests: 44 unit tests (`python3 -m unittest -v`) covering money parsing,
  unit-cost math, best/latest price selection, trends, recipe costing,
  CSV export, persistence, and the CLI. CI runs them on ubuntu-24.04 and
  windows-latest, Python 3.9 and 3.13.

## License

MIT - see [LICENSE](LICENSE). Copyright (c) 2026 Mehul Jhabak.
