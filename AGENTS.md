# Repository Guidelines

## Project Structure & Module Organization

This repository supports local research and migration of a dividend low-volatility strategy. `data/cache/` stores local Parquet exports from ClickHouse and includes `data/cache/README.md` with file-level provenance and usage notes. `docs/data_dictionary.md` documents field mappings, point-in-time assumptions, and migration guidance. `reference/` contains the original strategy script and source research materials; treat these as inputs, not generated outputs.

If you add runnable code, prefer a clear top-level package or script directory such as `src/` or `backtests/`, and keep tests in `tests/` with matching module names.

## Build, Test, and Development Commands

There is no project build system yet. Useful checks are currently file and data oriented:

- `rg --files --hidden -g '!/.git/**'` lists tracked and untracked project files.
- `python3 reference/79_红利低波_可以直接在聚宽做回测和模拟.py` runs the reference script only if its external 聚宽 dependencies are available.
- For new Python modules, add a reproducible command in this section or a README, for example `python3 -m pytest`.

Avoid regenerating large Parquet caches unless the data source, date range, and query are documented.

## Coding Style & Naming Conventions

Use Python 3 for research and backtest code. Follow PEP 8, four-space indentation, descriptive snake_case names, and small functions with explicit inputs. Keep stock symbols in the documented `SH600000`, `SZ000001`, or `BJxxxxx` format. Prefer structured readers such as pandas or Polars for Parquet data instead of ad hoc parsing.

## Testing Guidelines

No formal test suite exists yet. When adding logic, create focused tests under `tests/` named `test_<module>.py`. Cover date alignment, rolling-window calculations, symbol filters, and point-in-time joins, especially for fields documented in `docs/data_dictionary.md`. Include small fixtures rather than committing new large datasets.

## Commit & Pull Request Guidelines

Recent commits use short, direct messages such as `初始参考` and `Initial commit`. Continue with concise imperative summaries, in Chinese or English, that describe the main change.

Pull requests should include a brief purpose, changed data or code paths, validation commands run, and any assumptions about ClickHouse sources or cache freshness. Link related issues when available and include screenshots only for visual/reporting changes.

## Data & Configuration Notes

Treat `data/cache/*.parquet` as derived research data. Document source tables, export dates, and date ranges for any replacement cache. Do not commit credentials or local ClickHouse connection details.
