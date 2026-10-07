"""Industry-specific comparison templates.

Each template lists the metrics that matter most for a sector. Metrics come
from two places:
  * "trend"    - yearly ratios from financial statements (charted over time)
  * "snapshot" - current valuation figures from the quote (shown in a table)
"""

METRICS = {
    # key: (label, format, source, short explanation)
    "revenue_growth": ("Revenue growth", "pct", "trend", "Year-on-year change in total revenue."),
    "gross_margin": ("Gross margin", "pct", "trend", "Gross profit ÷ revenue: pricing power and production cost."),
    "net_margin": ("Net profit margin", "pct", "trend", "Net income ÷ revenue: how much of each dollar becomes profit."),
    "debt_to_equity": ("Debt-to-equity", "x", "trend", "Total debt ÷ shareholders' equity: financial leverage."),
    "roe": ("Return on equity", "pct", "trend", "Net income ÷ equity: return generated on shareholders' capital."),
    "roa": ("Return on assets", "pct", "trend", "Net income ÷ total assets: efficiency of the balance sheet."),
    "equity_to_assets": ("Equity-to-assets", "pct", "trend", "Equity ÷ total assets: a simple capital-strength proxy for banks."),
    "rnd_intensity": ("R&D intensity", "pct", "trend", "R&D spend ÷ revenue: investment in future products."),
    "pe": ("P/E ratio", "x", "snapshot", "Share price ÷ earnings per share."),
    "pb": ("Price-to-book", "x", "snapshot", "Share price ÷ book value per share; key for banks."),
    "dividend_yield": ("Dividend yield", "pct", "snapshot", "Annual dividend ÷ share price."),
    "beta": ("Beta", "num", "snapshot", "Sensitivity of the stock to market moves."),
    "market_cap": ("Market cap", "money", "snapshot", "Total market value of the shares."),
}

TEMPLATES = {
    "general": {
        "label": "General",
        "description": "Core growth, profitability and leverage measures for any company.",
        "metrics": ["revenue_growth", "net_margin", "debt_to_equity", "roe", "pe", "market_cap"],
    },
    "banking": {
        "label": "Banking & Financials",
        "description": "Banks are judged on returns and capital strength. Debt-to-equity and gross margin are not meaningful for banks, so they are left out.",
        "metrics": ["roe", "roa", "net_margin", "equity_to_assets", "revenue_growth", "pb", "pe", "dividend_yield"],
    },
    "technology": {
        "label": "Technology",
        "description": "Tech companies are judged on growth, margins and reinvestment in R&D.",
        "metrics": ["revenue_growth", "gross_margin", "net_margin", "rnd_intensity", "roe", "pe", "beta"],
    },
    "consumer": {
        "label": "Consumer & Retail",
        "description": "Steady consumer businesses are judged on margins, efficiency and income for investors.",
        "metrics": ["revenue_growth", "gross_margin", "net_margin", "roa", "debt_to_equity", "dividend_yield", "pe"],
    },
}

SECTOR_TO_TEMPLATE = {
    "Financial Services": "banking",
    "Technology": "technology",
    "Communication Services": "technology",
    "Consumer Defensive": "consumer",
    "Consumer Cyclical": "consumer",
}


def suggest_template(sectors: list[str]) -> str:
    """Pick the template that fits the most selected companies."""
    votes = {}
    for s in sectors:
        key = SECTOR_TO_TEMPLATE.get(s, "general")
        votes[key] = votes.get(key, 0) + 1
    if not votes:
        return "general"
    best = max(votes.items(), key=lambda kv: kv[1])
    # Only specialise when the companies agree on a sector.
    return best[0] if best[1] == len(sectors) else "general"


def template_payload(key: str) -> dict:
    tpl = TEMPLATES.get(key, TEMPLATES["general"])
    return {
        "key": key if key in TEMPLATES else "general",
        "label": tpl["label"],
        "description": tpl["description"],
        "metrics": [
            {"key": m, "label": METRICS[m][0], "format": METRICS[m][1], "source": METRICS[m][2], "help": METRICS[m][3]}
            for m in tpl["metrics"]
        ],
    }
