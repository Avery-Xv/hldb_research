from pathlib import Path

import matplotlib.pyplot as plt
import polars as pl


SERIES = {
    "Month-end trading day / open": (
        Path("outputs/backtest_hldb_2020_major_risks_fixed_open/nav.csv"),
        "#0B6E4F",
    ),
    "Month-end trading day / close": (
        Path("outputs/backtest_hldb_2020_major_risks_fixed_close/nav.csv"),
        "#2563A6",
    ),
    "Reference code calendar / open": (
        Path("outputs/backtest_hldb_2020_attribution_reference_calendar/nav.csv"),
        "#B54732",
    ),
}


def main() -> None:
    output_dir = Path("reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    combined: pl.DataFrame | None = None
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=160)
    for label, (path, color) in SERIES.items():
        nav = pl.read_csv(path, try_parse_dates=True).select(
            "trade_date", pl.col("nav").alias(label)
        )
        combined = nav if combined is None else combined.join(nav, on="trade_date", how="inner")
        final_nav = nav[label][-1]
        ax.plot(nav["trade_date"], nav[label], label=f"{label} ({final_nav:.2f})", color=color, linewidth=2)

    ax.set_title("Dividend Low-Volatility Strategy NAV Comparison", loc="left", fontsize=16, pad=16)
    ax.set_xlabel("Date")
    ax.set_ylabel("NAV (initial = 1.0)")
    ax.grid(axis="y", color="#D8DEE4", linewidth=0.8, alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(output_dir / "nav_comparison.png", bbox_inches="tight")
    plt.close(fig)

    assert combined is not None
    combined.write_csv(output_dir / "nav_comparison.csv")


if __name__ == "__main__":
    main()
