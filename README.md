# Outlet 451: Restaurant Ordering and Operations Platform

A Flask application for a restaurant. Customers order online and earn points. Staff run the dining room, kitchen, inventory, parties, payroll and finances from one console at `/staff`.

> **Status.** The code has been compile-checked but has not been through automated tests or a full end-to-end run. Test the flows in section 9 in a staging environment before taking real customers.

## Contents

1. What it does
2. Architecture
3. Project layout
4. Configuration reference
5. Run it locally
6. Deploy on Google Cloud (Cloud Run)
7. Deploy elsewhere (Render, Kubernetes, other clouds)
8. Deploy on a private server behind a VPN
9. Short user guide
10. Security notes
11. Known limitations

## 1. What it does

**Customers** (home page `/`)
- Register, browse the 21-item menu, order, and pay by cash, card or bKash.
- Earn 1 point per $1 paid. Redeem points at $0.05 each. Tax is 5%.
- Edit or cancel an order for 5 minutes after placing it. Print a receipt.

**Staff** (`/staff`): each role sees only its own tabs.

| Role | Tabs |
|---|---|
| Host | Host, Party reservation |
| Waiter, Cashier | Floor |
| Kitchen | Kitchen |
| Chef | Kitchen, Chef |
| Owner | Owner, Finance, Analytics, Guest analytics, Employees, Menu, Party reservation |
| Admin | Everything |

- **Host:** 13 seeded tables (ten 2-seat, three 15-seat; editable), seating, reservations with a 90-minute clash check.
- **Party reservation:** hold tables for a party, import a guest list from CSV or add guests by hand, auto-arrange seating, and check guests in and out with strict identity checks (section 9).
- **Floor:** one tab per table and per client. Waiters add items, pick up and deliver. Payment (cash or card terminal approval code) is only allowed once everything is served.
- **Kitchen and Chef:** dine-in queue grouped by table, with courses fired in order (starters, drinks, mains, desserts), chef rush, colour-coded status, ingredient stock and recipes. Starting an item deducts its ingredients.
- **Owner:** procurement, rent, lease and subscriptions, and an audit log.
- **Finance:** income statement, cash flow, balance sheet, daily report, expenses. These are simplified and not a substitute for an accountant.
- **Analytics:** sales, courses, tables, prep and delivery times, payroll. **Guest analytics** covers attendance, stay time and spend.
- **Employees:** staff records, monthly payroll runs, creating staff logins.
- **Menu:** CSV import, price and discount editing.

## 2. Architecture

```
Browser ──HTTPS──> Gateway / Load balancer
                      ├── /, /api/*            -> ordering service   (SERVICE=ordering)
                      └── /staff, /api/admin|kitchen|floor|owner|party
                                                -> back-office service (SERVICE=backoffice)
                              both ──> PostgreSQL (orders, tabs, guests, finance)
                              both ──> Redis (menu cache; optional)
```

- One image, two roles. `SERVICE=all` (default) runs everything in one process. `ordering` and `backoffice` run the two halves separately.
- The two services share one database. That is a stepping stone toward full microservices, not the end state.
- Layers: routes handle HTTP only. Business rules live in `services.py`, `floor.py` and `party.py`. Private helper methods keep pricing, stock, points and identity rules out of the routes.
- Sessions are signed cookies, so any number of instances can serve any user as long as they share `SECRET_KEY`.
- Redis only caches the menu. If `REDIS_URL` is unset, each instance uses its own in-memory cache.

## 3. Project layout

```
app/
  __init__.py        app factory, blueprint wiring, `flask seed` command
  models.py          database tables
  services.py        online orders, menu, payroll, finance statements
  floor.py           dine-in: tables, tabs, kitchen flow, inventory, payment, owner tools
  party.py           party reservations, guest list, check-in/out, guest analytics
  routes.py          customer and login routes
  backoffice.py      admin, kitchen and finance routes, /staff page
  dinein.py          floor and owner routes
  templates/         index.html (customers), staff.html (staff), receipts
menu.csv             the 21-item Outlet 451 menu
samples/             sample guest lists (65 and 19 guests)
Dockerfile           python:3.12-slim, non-root, gunicorn
compose.yaml         ordering + back-office + nginx gateway + PostgreSQL + Redis
nginx.conf           path routing for the compose gateway
k8s/deploy.yaml      two deployments, services, network policies, ingress
render.yaml          Render blueprint (single service)
```

## 4. Configuration reference

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes (prod) | PostgreSQL URL. `postgres://` and `postgresql://` are rewritten to the psycopg2 driver. Falls back to local SQLite if unset. |
| `SECRET_KEY` | Yes | Long random string. Must be identical on every instance. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | First run | Creates the first admin. Changing the password later does not change an existing admin. |
| `SEED` | First run | Set to `1` to create tables, the admin, the menu and the tables on start. |
| `REDIS_URL` | No | Menu cache. |
| `PROD` | Recommended | Set to `1` when served over HTTPS (secure cookies). |
| `SERVICE` | No | `all` (default), `ordering` or `backoffice`. |
| `EDIT_WINDOW_SECONDS` | No | Order edit and cancel window. Default 300. |
| `PORT` | No | Listen port. Default 8000. |

Seeding runs only on empty data: it never overwrites live menu prices or stock. Keep `SEED=1` for the first start only, because several instances starting at once can race while creating tables.

## 5. Run it locally

**Containers (recommended).** Works with Docker or Podman, including Fedora:

```bash
export SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
export ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD='choose-a-strong-one'
podman-compose up --build        # or: docker compose up --build
```

Open http://localhost:8080 for customers and http://localhost:8080/staff for staff. On Fedora with SELinux, change the nginx mount in `compose.yaml` to `./nginx.conf:/etc/nginx/nginx.conf:ro,Z`.

**Without containers (SQLite):**

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export FLASK_APP=app:create_app SECRET_KEY=dev ADMIN_EMAIL=a@x.io ADMIN_PASSWORD=change-me-123
flask seed && flask run
```

Check `GET /healthz` returns `ok`.

## 6. Deploy on Google Cloud (Cloud Run + Cloud SQL)

Cloud Run runs the container, Cloud SQL provides PostgreSQL, and Secret Manager holds credentials. Redis is optional, so this guide skips it. Check each service's current pricing and tier names before creating resources, because they change.

```bash
export PROJECT_ID=your-project REGION=asia-south1   # pick the region nearest your customers
gcloud auth login && gcloud config set project $PROJECT_ID
gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com
```

**1. Build the image**

```bash
gcloud artifacts repositories create outlet451 --repository-format=docker --location=$REGION
IMAGE=$REGION-docker.pkg.dev/$PROJECT_ID/outlet451/app:1.0.0
gcloud builds submit --tag $IMAGE        # run from the project folder
```

**2. Create the database**

```bash
gcloud sql instances create o451-db --database-version=POSTGRES_16 --region=$REGION \
  --edition=ENTERPRISE --tier=db-f1-micro        # see `gcloud sql tiers list` for current tiers
gcloud sql databases create o451 --instance=o451-db
gcloud sql users create o451 --instance=o451-db --password='STRONG_DB_PASSWORD'
gcloud sql instances patch o451-db --backup-start-time=02:00
```

**3. Store secrets.** URL-encode special characters in the database password.

```bash
python3 -c "import secrets;print(secrets.token_hex(32))" | gcloud secrets create o451-secret-key --data-file=-
printf '%s' 'postgresql+psycopg2://o451:STRONG_DB_PASSWORD@/o451?host=/cloudsql/'$PROJECT_ID:$REGION':o451-db' \
  | gcloud secrets create o451-db-url --data-file=-
printf '%s' 'STRONG_ADMIN_PASSWORD' | gcloud secrets create o451-admin-pw --data-file=-

SA=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')-compute@developer.gserviceaccount.com
for r in roles/secretmanager.secretAccessor roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding $PROJECT_ID --member=serviceAccount:$SA --role=$r; done
```

**4. First deploy (seeds the database, so keep a single instance)**

```bash
gcloud run deploy outlet451 --image=$IMAGE --region=$REGION --allow-unauthenticated \
  --add-cloudsql-instances=$PROJECT_ID:$REGION:o451-db --max-instances=1 \
  --set-env-vars=PROD=1,SEED=1,ADMIN_EMAIL=you@example.com \
  --set-secrets=SECRET_KEY=o451-secret-key:latest,DATABASE_URL=o451-db-url:latest,ADMIN_PASSWORD=o451-admin-pw:latest
```

Open the printed URL, then `/healthz`, then sign in at `/staff`.

**5. Switch to normal operation**

```bash
gcloud run deploy outlet451 --image=$IMAGE --region=$REGION --max-instances=5 --update-env-vars=SEED=0
```

**Notes**
- **Database connections:** each Gunicorn worker keeps a pool of up to 10 connections, and there are 3 workers per container. Small Cloud SQL tiers allow few connections. Lower `pool_size` in `app/__init__.py`, or use fewer instances, or add a connection pooler.
- **Domain and HTTPS:** Cloud Run provides HTTPS. For a custom domain, use a domain mapping or an HTTPS load balancer.
- **Restricting staff access:** a load balancer with Identity-Aware Proxy or Cloud Armor in front of `/staff` and the staff API paths adds a second lock. This needs the two-service split in section 2.
- **Cost:** Cloud Run can scale to zero, but the Cloud SQL instance bills while it exists.

## 7. Deploy elsewhere

**Render (free tier, demo only).** Push the repo to GitHub or GitLab, choose New, then Blueprint, and select it. `render.yaml` creates one web service, a free Postgres database and a free Key Value cache. Fill in `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Limits: free services sleep after about 15 minutes idle, free databases expire after 30 days, and free web services cannot talk to each other privately, so leave `SERVICE` unset. Use paid plans for real customers.

**Kubernetes.** `k8s/deploy.yaml` defines two deployments (ordering and back-office), services, network policies and an ingress. Steps:
1. Push the image to your registry and put its name in the file in place of `registry.example.com/outlet451:1.0.0`.
2. Create the secret: `kubectl create secret generic outlet451-secrets --from-literal=DATABASE_URL=... --from-literal=SECRET_KEY=... --from-literal=ADMIN_EMAIL=... --from-literal=ADMIN_PASSWORD=... --from-literal=PROD=1 --from-literal=SEED=1`.
3. Replace `orders.example.com` and create the `outlet451-tls` certificate secret (for example with cert-manager).
4. `kubectl apply -f k8s/deploy.yaml`, then set `SEED=0` and roll the pods once the database is seeded.

Pods run as non-root with a read-only filesystem and dropped capabilities. Use a managed PostgreSQL service rather than a database inside the cluster.

**Other clouds.** Any platform that runs a container and gives you PostgreSQL works the same way:

| Need | AWS | Azure | DigitalOcean |
|---|---|---|---|
| Run the container | App Runner or ECS Fargate | Container Apps | App Platform |
| PostgreSQL | RDS | Azure Database for PostgreSQL | Managed Databases |
| Redis (optional) | ElastiCache | Azure Cache for Redis | Managed Redis/Valkey |
| Secrets | Secrets Manager | Key Vault | App-level secrets |

Set the variables from section 4, use `SEED=1` for the first start only, and send traffic to port `$PORT`.

## 8. Deploy on a private server behind a VPN

Goal: customers reach only the ordering site over the internet, while staff reach `/staff` only through a WireGuard tunnel. Example on a Fedora server.

**1. Packages and firewall**

```bash
sudo dnf install -y podman podman-compose wireguard-tools caddy
sudo firewall-cmd --permanent --add-service=http --add-service=https --add-port=51820/udp
sudo firewall-cmd --reload
```

**2. WireGuard.** Generate keys with `wg genkey | tee server.key | wg pubkey > server.pub` (repeat for each staff device). In `/etc/wireguard/wg0.conf`:

```
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = <server.key contents>

[Peer]            # one block per staff device
PublicKey = <device public key>
AllowedIPs = 10.8.0.2/32
```

On each device use `Address = 10.8.0.2/32`, a peer with the server's public key, `Endpoint = <server public IP>:51820`, and `AllowedIPs = 10.8.0.1/32` so only office traffic uses the tunnel. Start it: `sudo systemctl enable --now wg-quick@wg0`.

**3. Run the services.** Create `compose.vpn.yaml` next to `compose.yaml`:

```yaml
services:
  ordering:
    ports: ["127.0.0.1:8081:8000"]
    environment: {PROD: "1"}
  backoffice:
    ports: ["10.8.0.1:8082:8000"]
```

```bash
podman-compose -f compose.yaml -f compose.vpn.yaml up -d ordering backoffice db cache
```

Start WireGuard first so the VPN address exists. The gateway container is not used here. Do not set `PROD` on the back-office service, because its secure cookie would not be sent over plain HTTP. Traffic to it is already encrypted by the tunnel.

**4. Public HTTPS for customers.** In `/etc/caddy/Caddyfile`:

```
orders.example.com {
    reverse_proxy 127.0.0.1:8081
}
```

Run `sudo systemctl enable --now caddy`. Caddy obtains the certificate automatically once DNS points at the server. Staff open `http://10.8.0.1:8082/staff` while connected to the VPN.

**5. Operations.** Back up with `podman exec <db container> pg_dump -U o451 o451 > backup.sql` on a schedule, keep the server patched, and use Podman Quadlet (systemd units) so containers start on boot.

## 9. Short user guide

**First login.** Sign in at `/staff` with `ADMIN_EMAIL` and `ADMIN_PASSWORD`. In the Employees tab, use Create staff login to add people (host, waiter, kitchen, chef, cashier, owner, admin). Give each person their own login. If you lose the admin password, set a new `ADMIN_EMAIL` and `ADMIN_PASSWORD` and redeploy with `SEED=1`.

**A table's journey**
1. **Host** seats the party on the Host tab.
2. **Waiter** adds items to each guest's tab on the Floor tab.
3. **Kitchen** presses Start, then Ready. Courses go in order, and an item shows "Waiting for earlier course" until the earlier course is served. **Chef** can Rush an item.
4. **Waiter** presses Pick up, then Deliver.
5. **Cashier or waiter** takes payment once everything is served: cash (enter the amount tendered) or card (enter the terminal's approval code). Print the receipt.

**Status colours.** Red is waiting, amber is cooking, green is ready to pick up, teal is on the way, blue is served.

**Parties**
1. **Host:** Party reservation tab, choose a name, date and tables, then Reserve tables.
2. Import the guest list (sample files are in `samples/`). Then press Auto-arrange, or set each guest's table and seat. Only operational columns are imported. Addresses, travel, gifts and contact details are ignored.
3. **Admin:** press Issue passes. One-time codes appear once for printing. Each guest keeps theirs.
4. **Host at the door:** tick "Photo ID checked", type the guest's code and press Check in. Three wrong codes lock the guest until an admin presses Reset pass. The guest then gets a tab called "Privileged #n" for waiters to order on.
5. **Check out** is allowed only after the guest's tab is served and paid. Mark absentees as No-show. When every guest has left or been marked no-show, the party becomes **OPEN TO ALL** and its tables are released.

**Owner and admin.** Receive stock in Owner (this also records the cost). Add rent, lease and subscriptions, and post them each month. Finance holds the statements and expense entry. Employees runs payroll for a month. Menu imports a CSV with the columns `name,category,description,price,discount_pct,stock`.

**Common messages**

| Message | What to do |
|---|---|
| Earlier course is still in progress | Serve the earlier course first |
| Not enough ... in stock | Receive stock in Owner or fix the recipe |
| All items must be served before payment | Deliver the remaining items |
| Table is held for a party reservation | Use the Party reservation tab |
| Edit window closed | The 5 minutes have passed |

## 10. Security notes

- Passwords are hashed. Staff roles limit every staff route on the server, not only the tabs shown.
- Guest pass codes are stored only as keyed hashes. Check-in needs ID confirmation and the code, locks after three failures, and is audited.
- The audit log records order events, paid tabs, purchases, pass issuing and every check-in attempt.
- Cards are never typed into this app. Dine-in cards go through a separate terminal, and only the approval code is recorded.
- Keep secrets in a secret manager, never in the repo. Serve over HTTPS and set `PROD=1`.
- Containers run as non-root. In production, scan images and pin dependency versions in CI.

## 11. Known limitations

- **Online card and bKash payments are placeholders.** They are not verified with any payment provider. A real gateway needs a redirect-and-verify flow before you take online payments.
- There are no database migrations yet (Alembic is recommended). New tables are created automatically, but changes to existing tables are not.
- There are no automated tests and no login rate limiting.
- No ingredients or recipes are seeded. The chef enters them, and items without a recipe use no stock.
- Reservation and party times are stored exactly as typed, with no time-zone conversion.
- The finance statements are simplified. Inventory is not valued, and the loyalty-points liability uses current balances.
- The two services share one database, so they are not independently deployable data-wise.
