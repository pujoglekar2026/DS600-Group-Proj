# Nimbus RM

**A web app for relationship managers to compare companies and keep track of client conversations — hosted on AWS.**

Group 1 project, extending the Nimbus case study. A relationship manager (RM) can compare two or more companies side by side, save a snapshot of each client discussion (price at the time, client interests, follow-up date), and look those records up later to prepare for the next conversation.

![Comparison page](docs/screenshots/compare.png)

---

## Features

### Core (from the proposal)

| Feature | What the RM can do | Workshop link |
|---|---|---|
| **Compare companies** | Pick 2–5 tickers and see their price performance on one chart, rebased to 100 or in actual prices, over 1 month to 5 years | Lesson 4, Feature 2 |
| **Record discussion snapshots** | Save client ID, ticker, date, stock price at the time of discussion (fetched automatically), interest level, topic tags and notes | Lesson 4, Features 3 & 4 |
| **Retrieve records** | Find past discussions by client or by company, search notes, filter by date or interest, export to CSV | Stored in Amazon RDS |

### Advanced features

- **Financial ratios and trends** — revenue growth, gross and net margin, debt-to-equity, ROE, ROA and R&D intensity, calculated from each company's annual income statement and balance sheet and charted over the last 4–5 years.
- **Industry-specific comparison templates** — *General*, *Banking & Financials*, *Technology* and *Consumer & Retail*. Each template shows the metrics that matter for that sector (e.g. banks use ROE, ROA, price-to-book and equity-to-assets; debt-to-equity is left out because it is not meaningful for banks). The app suggests a template automatically from the companies' sectors.
- **Risk and diversification analytics** — annualised return, volatility, maximum drawdown and a correlation matrix of daily returns, so an RM can talk about risk and diversification, not just returns.
- **"Since discussion" tracking** — each client's history shows the price when you spoke and the change since then, a ready-made talking point for the follow-up call.
- **Follow-up reminders** — the dashboard lists follow-ups due in the next 14 days and flags overdue ones.
- **Multiple RMs with private books** — each RM logs in and sees only their own clients and records.
- **Resilient market data** — live data from Yahoo Finance via `yfinance`, cached for 15 minutes. If Yahoo is unavailable (it often rate-limits cloud servers), the app switches to clearly labelled demo data so a presentation never breaks.

---

## Architecture

```mermaid
flowchart LR
    U[RM's browser] -->|HTTPS| CF[CloudFront]
    subgraph VPC["Amazon VPC 10.0.0.0/16"]
        subgraph PUB["Public subnet 10.0.1.0/24"]
            EC2["EC2 · Amazon Linux 2023<br/>Nginx → Gunicorn → Flask"]
        end
        subgraph PRIV["Private subnets 10.0.3.0/24 + 10.0.4.0/24"]
            RDS[("Amazon RDS<br/>MySQL")]
        end
        EC2 -->|MySQL :3306<br/>allowed by DB security group| RDS
    end
    CF -->|HTTP :80 + origin token| EC2
    EC2 -->|Internet egress| IGW[Internet Gateway]
    EC2 -->|HTTPS| YF[Yahoo Finance]
    IAM[IAM instance profile] -.-> EC2
```

| AWS service | How Nimbus RM uses it |
|---|---|
| **Amazon CloudFront** | Public entry point with HTTPS from its default `cloudfront.net` certificate. Dynamic caching is disabled and session cookies/query parameters are forwarded. Nginx requires a secret origin header to deter direct-origin access. |
| **Amazon VPC** | One public subnet hosts the app; two private subnets in separate Availability Zones are used for the RDS subnet group. Only the public subnet routes through the Internet Gateway; the database has no public route. |
| **Amazon EC2** | A small Amazon Linux 2023 instance runs Nginx and Gunicorn/Flask as a `systemd` service. It needs outbound internet access for installation and Yahoo Finance; a NAT gateway is avoided to reduce sandbox cost. |
| **Amazon RDS** | Single-AZ MySQL database in the private subnets holds users, clients and discussion snapshots. The database endpoint is not public and its security group accepts MySQL only from the EC2 security group. |
| **Amazon S3** | A private, encrypted archive bucket stores snapshots with a `discussed_on` date older than five years; objects transition to Glacier Flexible Retrieval after 30 days. |
| **IAM** | The EC2 instance uses the sandbox's existing `LabInstanceProfile` for Systems Manager and, if permitted by its existing policies, S3 archive access. The sandbox does not allow creating IAM roles or policies. |

The deployment is defined in [`infra/nimbus-merged-stack.yaml`](infra/nimbus-merged-stack.yaml). Its architecture, setup steps, resource rationales and limitations are documented in the [CloudFront sandbox guide](docs/CLOUDFRONT_SANDBOX.md), with the lab's full restrictions in [AWS Academy sandbox limitations](docs/AWS_ACADEMY_SANDBOX_LIMITATIONS.txt). Old discussion snapshots are archived monthly to a private S3 bucket and transitioned to Glacier Flexible Retrieval.

---

## Project structure

```
DS600-Group-Proj/
├── app/
│   ├── __init__.py        # App factory, CSRF protection, template filters
│   ├── config.py          # Settings from environment variables (.env)
│   ├── models.py          # Database tables: users, clients, snapshots
│   ├── market.py          # Market data (Yahoo + demo), caching, analytics
│   ├── industry.py        # Industry comparison templates and metric definitions
│   ├── api.py             # JSON API used by the comparison page
│   ├── routes.py          # HTML pages: dashboard, clients, discussions
│   ├── auth.py            # Login, registration, logout
│   ├── cli.py             # flask init-db / create-user / seed-demo / archive-snapshots
│   ├── archive.py         # Verified export of old snapshots to S3
│   ├── templates/         # Jinja2 HTML templates
│   └── static/            # CSS and the comparison page's JavaScript (Chart.js)
├── infra/
│   └── nimbus-merged-stack.yaml # CloudFront sandbox stack with S3 snapshot archive
├── deploy/
│   ├── nginx-cloudfront.conf # Origin-token guard for the sandbox CloudFront deployment
│   ├── setup_ec2.sh       # Installs and starts the app on EC2
│   ├── update.sh          # Pulls new code from GitHub and restarts
│   ├── nimbus.service     # systemd unit for Gunicorn
│   ├── nimbus-archive.service # systemd service for snapshot archiving
│   └── nimbus-archive.timer # Monthly archive schedule
├── docs/
│   ├── CLOUDFRONT_SANDBOX.md # Sandbox deployment steps, rationale, considerations
│   ├── AWS_ACADEMY_SANDBOX_LIMITATIONS.txt # AWS Academy lab restrictions and access instructions
│   ├── schema.sql         # Database schema (for reference/report)
│   └── screenshots/
├── tests/                 # pytest suite (runs offline)
├── .github/workflows/ci.yml # Runs tests on every push
├── requirements.txt
├── .env.example
└── wsgi.py                # Entry point
```

---

## Run it on your laptop

Requires Python 3.10 or newer.

```bash
git clone https://github.com/pujoglekar2026/DS600-Group-Proj.git
cd DS600-Group-Proj
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Leave `DB_HOST` empty in `.env` and the app uses a local SQLite file, so you don't need MySQL locally.

```bash
flask --app wsgi seed-demo         # creates login demo / demo12345 with sample clients
flask --app wsgi run --debug
```

Open http://127.0.0.1:5000 and log in as **demo / demo12345**. Set `MARKET_DATA_PROVIDER=demo` in `.env` to work completely offline.

Other commands:

```bash
flask --app wsgi create-user alice "Alice Tan"   # prompts for a password
flask --app wsgi init-db                          # create tables
python -m pytest -q                               # run the tests
```

---

## Deploy to AWS

For the AWS Academy sandbox deployment with CloudFront, follow **[docs/CLOUDFRONT_SANDBOX.md](docs/CLOUDFRONT_SANDBOX.md)**. In short:

1. Push this repository to GitHub (public, so EC2 can clone it).
2. In **us-east-1**, create a CloudFormation stack from `infra/nimbus-merged-stack.yaml` and enter the repository URL and generated secrets.
3. Wait for CloudFront to report **Deployed**, then open the `ApplicationURL` stack output.
4. Use Systems Manager Session Manager to seed the demo login on EC2.

To ship a code change: push to GitHub, then on the server run `sudo bash /opt/nimbus/deploy/update.sh`.

---

## Using the app

1. **Compare** — enter tickers such as `D05.SI, O39.SI, U11.SI` (Singapore stocks end in `.SI`) or click a preset. Switch the chart between *Rebased to 100* and *Actual price*, then scroll to the summary, correlation matrix, and the ratio charts. Change the **Industry template** to see different metrics.
2. **Save** — click *Save D05.SI @ 24.90* at the bottom of the comparison, pick or create a client, add notes, tags and a follow-up date.
3. **Retrieve** — open **Clients** to see a client's full history with the price change since each discussion, or **Discussions** to filter across all clients by company, interest, date or keywords, and export to CSV.

| Dashboard | Client history |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Client](docs/screenshots/client.png) |

---

## Security measures

- Database in private subnets, `PubliclyAccessible: false`, reachable only from the web server's security group; storage encrypted at rest.
- Passwords hashed with Werkzeug (PBKDF2/scrypt); sessions signed with `SECRET_KEY`; cookies `HttpOnly` and `SameSite=Lax`.
- CSRF token on every form; all database access through SQLAlchemy (parameterised queries); ticker input validated against a strict pattern.
- Every record is scoped to the logged-in RM — another RM gets a 404 for records they don't own (covered by tests).
- Secrets live in `.env` (mode 600, owned by the service user) and are never committed (`.gitignore`).

## Limitations and next steps

- **HTTPS** — the app is served over HTTP. Next step: an Application Load Balancer with an AWS Certificate Manager certificate and a domain in Route 53.
- **Secrets** — the database password is passed as a CloudFormation parameter. In production it should come from AWS Secrets Manager.
- **Availability** — one EC2 instance and a single-AZ database. Next step: an Auto Scaling group across both public subnets behind the load balancer, and RDS Multi-AZ.
- **Market data** — Yahoo Finance via `yfinance` is free but unofficial and may be delayed or rate-limited. A bank would use a licensed feed (Bloomberg, Refinitiv).
- Statement data covers the last 4–5 fiscal years; some companies (especially banks) don't report every line item, so some ratios show "—".

*Market data is for discussion purposes only and is not investment advice.*
