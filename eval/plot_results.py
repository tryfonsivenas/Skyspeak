"""Figure for the paper: correct outcomes by category, Greek and English panels.
Reads eval/results/summary_by_category.csv (python -m eval.summarize first).
    python -m eval.plot_results            -> paper/fig/results_en.png and results_el.png
"""
import csv, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "eval", "results", "summary_by_category.csv")
# colour follows the run (fixed slot order, validated: blue, orange, aqua, yellow, magenta, green)
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
PROV_ORDER = ["mock", "openai", "anthropic", "ollama"]
NAMES = {"en": {"mock": "Rule-based baseline"}, "el": {"mock": "Αναλυτής κανόνων"}}


def label(run, lang_ui):
    prov, _, model = run.partition(":")
    if prov == "mock":
        return NAMES[lang_ui]["mock"]
    pretty = {"openai": "OpenAI", "anthropic": "Anthropic",
              "ollama": "Local" if lang_ui == "en" else "Τοπικό"}.get(prov, prov)
    return f"{pretty} {model}"


CATS = {"en": ["A\nsingle", "B\nsequence", "C\nshape", "D\nrepetition", "E\nsafe outcome"],
        "el": ["Α\nενέργεια", "Β\nακολουθία", "Γ\nσχήμα", "Δ\nεπανάληψη", "Ε\nασφάλεια"]}
TXT = {"en": dict(panels=["Greek instructions", "English instructions"], y="Correct (%)"),
       "el": dict(panels=["Ελληνικές οδηγίες", "Αγγλικές οδηγίες"], y="Σωστά (%)")}
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"

def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    runs = [r for r in dict.fromkeys(r["run"] for r in rows) if "|" not in r]   # main set, default prompt only
    def size(run):
        import re
        m = re.search(r"(\d+(?:\.\d+)?)b\b", run.lower())
        return float(m.group(1)) if m else 0.0
    runs.sort(key=lambda r: (PROV_ORDER.index(r.split(":")[0]) if r.split(":")[0] in PROV_ORDER else 9, size(r), r))
    runs = runs[:len(SLOTS)]
    for lang_ui in ("en", "el"):
        fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), sharey=True)
        n = len(runs); w = 0.8 / n
        for ax, lang, title in zip(axes, ("el", "en"), TXT[lang_ui]["panels"]):
            for k, run in enumerate(runs):
                vals = [float(next(r for r in rows if r["run"] == run and r["cat"] == c and r["lang"] == lang)["correct_%"] or 0) for c in "ABCDE"]
                xs = [i + (k - (n - 1) / 2) * w for i in range(5)]
                bars = ax.bar(xs, vals, width=w * 0.92, color=SLOTS[k], label=label(run, lang_ui), zorder=2,
                              edgecolor="white", linewidth=0.8)
                if n <= 2:   # with more series the values live in Table 3 instead
                    for x, v in zip(xs, vals):
                        ax.text(x, v + 2, f"{v:.0f}", ha="center", va="bottom", fontsize=8, color=INK)
            ax.set_title(title, fontsize=10, color=INK, loc="left")
            ax.set_xticks(range(5)); ax.set_xticklabels(CATS[lang_ui], fontsize=8, color=MUTED)
            ax.set_ylim(0, 112); ax.set_yticks([0, 25, 50, 75, 100])
            ax.tick_params(axis="y", labelsize=8, colors=MUTED, length=0); ax.tick_params(axis="x", length=0)
            ax.grid(axis="y", color=GRID, lw=0.8, zorder=0)
            for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
            ax.spines["bottom"].set_color(MUTED)
            ax.axvline(3.5, color=MUTED, lw=0.8, ls=(0, (3, 3)))
        axes[0].set_ylabel(TXT[lang_ui]["y"], fontsize=9, color=MUTED)
        if n >= 2:
            fig.legend(*axes[0].get_legend_handles_labels(), frameon=False, fontsize=8, loc="lower center", ncol=min(n, 3))
            fig.subplots_adjust(bottom=0.28)
        if n < 2:
            fig.tight_layout()
        out = os.path.join(ROOT, "paper", "fig", f"results_{lang_ui}.png")
        fig.savefig(out, dpi=220); plt.close(fig); print(out)

if __name__ == "__main__":
    main()
