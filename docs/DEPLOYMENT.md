# Deploying Nimbus RM to AWS

> **AWS Academy sandbox with CloudFront:** Use
> [`CLOUDFRONT_SANDBOX.md`](CLOUDFRONT_SANDBOX.md) and
> [`../infra/nimbus-cloudfront-sandbox.yaml`](../infra/nimbus-cloudfront-sandbox.yaml).
> The older CloudFormation and console procedures below describe the original
> deployment and include resources/settings that may not be permitted in the
> sandbox.

This guide describes the original general-purpose AWS architecture. The
CloudFormation template and manual setup below may use features the AWS
Academy sandbox does not allow. For that sandbox, use the dedicated
[CloudFront sandbox guide](CLOUDFRONT_SANDBOX.md) instead.

This guide covers two ways to deploy:

- **Option A — CloudFormation (recommended).** One template builds the VPC, subnets, security groups, RDS database and EC2 server, and installs the app. About 15 minutes, mostly waiting.
- **Option B — By hand in the AWS console.** The same architecture, built click by click. Slower, but useful for understanding each service and for screenshots in your report.

The procedures below are intended for a normal AWS account; they are retained
as reference material for the original architecture.

---

## Step 0 — Put the code on GitHub

1. Create a new repository on GitHub, e.g. `nimbus-rm`. Make it **Public** (EC2 clones it without a password).
   *If it must be private, see "Private repository" at the end.*
2. From the project folder on your laptop:

   ```bash
   git init
   git add .
   git commit -m "Nimbus RM: initial version"
   git branch -M main
   git remote add origin https://github.com/<your-username>/nimbus-rm.git
   git push -u origin main
   ```

3. Check on GitHub that `.env` and `nimbus_local.db` were **not** uploaded (`.gitignore` excludes them).
4. Add teammates under **Settings → Collaborators** so everyone can push.

---

## Option A — CloudFormation

### 1. Start the lab and open the console
🎓 In Learner Lab, click **Start Lab**, wait for the dot to turn green, then click **AWS**. Stay in **us-east-1 (N. Virginia)**. Download the SSH key from **AWS Details → Download PEM** (`labsuser.pem`).

Normal account: create a key pair under **EC2 → Key pairs** and download it.

### 2. Generate a secret key
On your laptop:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output.

### 3. Create the stack
1. Open **CloudFormation → Create stack → With new resources (standard)**.
2. Choose **Upload a template file**, select `infra/nimbus-stack.yaml`, click **Next**.
3. Stack name: `nimbus`.
4. Parameters:

   | Parameter | Value |
   |---|---|
   | RepoUrl | `https://github.com/<your-username>/nimbus-rm.git` |
   | RepoBranch | `main` |
   | AppSecretKey | the string from step 2 |
   | DBUsername | `admin` |
   | DBPassword | a strong password, letters/digits and `!#%^*_+=-` only — **write it down** |
   | DBInstanceClass | `db.t3.micro` |
   | InstanceType | `t3.micro` |
   | KeyName | 🎓 `vockey` (otherwise your key pair) |
   | SSHAllowedCidr | your IP + `/32` (search "what is my IP"), or `0.0.0.0/0` for a class demo |
   | InstanceProfileName | 🎓 `LabInstanceProfile`. Normal account: **clear the field** so the stack creates its own IAM role |

5. Click **Next**, leave options as they are, click **Next**.
6. Normal account only: tick **I acknowledge that AWS CloudFormation might create IAM resources**.
7. Click **Submit**.

### 4. Wait
The RDS database takes 5–10 minutes, then EC2 installs the app (about 3–5 minutes). The stack shows **CREATE_COMPLETE** when the app has passed its health check.

### 5. Open the app
Go to the stack's **Outputs** tab and open **WebsiteURL**. You should see the login page.

### 6. Create logins
SSH in using the `SSHCommand` from Outputs (run it from the folder containing your key):

```bash
chmod 400 labsuser.pem
ssh -i labsuser.pem ec2-user@<public-dns>
```

Then on the server:

```bash
cd /opt/nimbus
# Demo account with sample clients and discussions (good for the presentation):
sudo -u nimbus .venv/bin/flask --app wsgi seed-demo
# A real account for each teammate:
sudo -u nimbus .venv/bin/flask --app wsgi create-user aastha "Aastha"
```

RMs can also sign up from the login page (**Create an account**).

---

## Option B — By hand in the console

Use the same names so your screenshots match the architecture diagram.

### B1. VPC and subnets
1. **VPC → Create VPC → VPC and more.**
2. Name tag: `nimbus`. IPv4 CIDR: `10.0.0.0/16`.
3. Availability Zones: **2**. Public subnets: **2**. Private subnets: **2**.
4. NAT gateways: **None** (the database doesn't need internet access, and NAT costs money). VPC endpoints: **None**.
5. Keep **Enable DNS hostnames** and **Enable DNS resolution** ticked. Click **Create VPC**.

This creates the subnets, the Internet Gateway, and the public and private route tables for you.

6. Open **Subnets**, select each **public** subnet → **Actions → Edit subnet settings** → tick **Enable auto-assign public IPv4 address**.

### B2. Security groups
**EC2 → Security Groups → Create security group** (choose the `nimbus` VPC each time):

- `nimbus-web-sg` — Inbound: **HTTP 80** from `0.0.0.0/0`; **SSH 22** from *My IP*.
- `nimbus-db-sg` — Inbound: **MYSQL/Aurora 3306**, Source = **custom → `nimbus-web-sg`** (pick the security group, not an IP range).

### B3. RDS database
1. **RDS → Subnet groups → Create DB subnet group**: name `nimbus-db-subnets`, VPC `nimbus`, both AZs, select the two **private** subnets.
2. **RDS → Create database → Standard create**:
   - Engine **MySQL**; Templates **Free tier** (or **Dev/Test**)
   - DB instance identifier `nimbus-db`; master username `admin`; set a password
   - Instance class `db.t3.micro`; storage 20 GiB gp2; untick storage autoscaling
   - VPC `nimbus`; subnet group `nimbus-db-subnets`; **Public access: No**
   - Existing security group: **`nimbus-db-sg`** (remove `default`)
   - **Additional configuration → Initial database name: `nimbus`** ← don't skip this
   - Untick Enhanced Monitoring
3. Create, and wait until the status is **Available**. Copy the **Endpoint** from the *Connectivity & security* tab.

### B4. EC2 web server
1. **EC2 → Launch instance**: name `nimbus-web`, AMI **Amazon Linux 2023**, type `t3.micro`, key pair 🎓 `vockey`.
2. **Network settings → Edit**: VPC `nimbus`, a **public** subnet, auto-assign public IP **Enable**, existing security group **`nimbus-web-sg`**.
3. **Advanced details → IAM instance profile**: 🎓 `LabInstanceProfile`.
4. Launch, then SSH in (see A6) and run:

   ```bash
   sudo dnf install -y git
   sudo git clone https://github.com/<your-username>/nimbus-rm.git /opt/nimbus
   sudo cp /opt/nimbus/.env.example /opt/nimbus/.env
   sudo nano /opt/nimbus/.env
   ```

   Fill in `SECRET_KEY`, `DB_HOST` (the RDS endpoint), `DB_USER=admin`, `DB_PASSWORD`, `DB_NAME=nimbus`. Save with Ctrl+O, Enter, Ctrl+X.

   ```bash
   sudo bash /opt/nimbus/deploy/setup_ec2.sh
   ```

   It ends with `Nimbus RM is running.` Then create logins as in A6 and open `http://<EC2 public DNS>` in your browser.

---

## Updating the app after a code change

```bash
# On your laptop
git add . && git commit -m "Describe the change" && git push

# On the EC2 instance
sudo bash /opt/nimbus/deploy/update.sh
```

---

## 🎓 Learner Lab notes

- When the lab session ends, AWS **stops** EC2 and RDS. After **Start Lab** they start again, and the app starts automatically (systemd), but RDS can take a few minutes — give it 5 minutes before testing.
- The EC2 **public IP and DNS name change** after every restart. Check the new address in the EC2 console (or attach an **Elastic IP** to keep it fixed for the demo).
- Budget: `t3.micro` + `db.t3.micro` costs a few dollars of credit per day running. Delete the stack when the project is graded.

---

## Checking it works

| Check | How |
|---|---|
| App is healthy | `curl http://<public-dns>/api/health` → `{"database": true, "status": "ok"}` |
| Database is private | RDS console shows *Publicly accessible: No*; connecting from your laptop times out |
| DB only accepts the web tier | `nimbus-db-sg` inbound rule source is `nimbus-web-sg` |
| Data is in RDS | On EC2: `mysql -h <rds-endpoint> -u admin -p nimbus -e "SELECT COUNT(*) FROM snapshots;"` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Stack fails at **WebServer** | SSH in and read `/var/log/nimbus-bootstrap.log`. The most common cause is a wrong or private `RepoUrl`. |
| `502 Bad Gateway` | The app isn't running: `sudo journalctl -u nimbus -n 100`, then `sudo systemctl restart nimbus`. |
| "could not reach the database" | Check `DB_HOST` in `/opt/nimbus/.env`, that RDS is *Available*, and that `nimbus-db-sg` allows 3306 from `nimbus-web-sg`. |
| `Unknown database 'nimbus'` (Option B) | You skipped *Initial database name*. Run `mysql -h <endpoint> -u admin -p -e "CREATE DATABASE nimbus;"` then `sudo bash /opt/nimbus/deploy/setup_ec2.sh`. |
| Yellow "demo data" banner | Yahoo Finance blocked or rate-limited the server's IP. It usually clears after a while; demo data keeps the app usable meanwhile. |
| SSH times out | Your IP changed — update the SSH rule in `nimbus-web-sg`. |
| SSH "permissions too open" | `chmod 400 labsuser.pem` |

## Private repository
Create a GitHub **fine-grained personal access token** with read-only *Contents* access to the repo, and use `https://<token>@github.com/<user>/nimbus-rm.git` as the `RepoUrl`. Treat the token like a password and delete it after the project.

## Cleaning up
**CloudFormation → nimbus → Delete** removes everything the stack created. For Option B, delete in this order: EC2 instance → RDS database (untick *final snapshot*) → DB subnet group → security groups → VPC.
