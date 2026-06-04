# fermlog

A personal fermentation kitchen tracker. Track ferments, batches, ingredients, additives, log entries, schedules, and tools — all in one place.

Built with FastAPI, SQLAlchemy, Jinja2, and SQLite. Designed to run self-hosted on a NAS.

---

## Features

- **Ferments & Batches** — track fermentation projects with full batch history, lot codes, and stage tracking
- **Log entries** — record pH, temperature, smell/visual descriptors, and status changes per batch
- **Ingredients & Additives** — manage your ingredient and additive library with tags and types
- **Schedules** — reminders for feeding, maintenance, and calibration with overdue tracking
- **Tools** — track equipment with maintenance schedules
- **Dashboard** — overview of active ferments, attention items, and due schedules
- **Users** — role-based access (admin / editor / viewer)
- **Settings** — manage all lookup tables (statuses, categories, additive types, etc.)
- **Mobile-friendly** — responsive layout with hamburger nav

---

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 |
| Frontend | Jinja2 templates, HTMX, Flatpickr |
| Database | SQLite |
| Package manager | uv |
| Hosting | Docker on Ugreen NAS, Tailscale for remote access |

---

## Local Development

**Requirements:** Python 3.13, uv

```bash
git clone https://github.com/lorerlrolerl/fermlog.git
cd fermlog
uv sync
cp .env.example .env   # edit as needed
make run               # starts at http://localhost:8000
```

**Common commands:**

```bash
make run        # start dev server with reload
make test       # run test suite
make test-cov   # tests with coverage report
make seed       # seed database with sample data
make reset      # drop and recreate database
make backup     # backup ferm.db to backups/
make lint       # lint with ruff
make lint-fix   # auto-fix lint issues
```

**Environment variables** (`.env`):

```
APP_NAME=fermlog
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:///./ferm.db
```

---

## Deployment (NAS)

The app runs in Docker on a Ugreen NAS, accessible remotely via Tailscale.

**Docker setup:**

```bash
# On the NAS
cd /volume1/docker/fermlog
cp .env.example .env   # set SECRET_KEY and DATABASE_URL
docker compose up -d --build
```

`.env` on the NAS:
```
APP_NAME=fermlog
SECRET_KEY=your-secret-key
DATABASE_URL=sqlite:////app/data/ferm.db
WEBHOOK_SECRET=your-webhook-secret
```

**Webhook deploy service** (systemd):

A lightweight Python webhook server runs as a systemd service on the NAS host. It receives deploy triggers from GitHub Actions and runs `git pull + docker compose up --build` automatically.

```bash
sudo systemctl status fermlog-webhook   # check status
tail -f /volume1/docker/fermlog/logs/webhook.log   # watch logs
```

**Remote access:** Install Tailscale on the NAS and your devices. Access the app at `http://NAS-TAILSCALE-IP:8000`.

---

## CI/CD

Three GitHub Actions workflows:

| Workflow | Triggers | Actions |
|---|---|---|
| `ci.yml` | Push to `feature/*`, `development`, PR to `main`/`development` | Lint (ruff) + tests |
| `deploy.yml` | Push to `main` | Tests → deploy to NAS via webhook |
| `release.yml` | Push to `main` | Auto-tag + GitHub Release (semantic versioning) |

**Skipped automatically** when all commits are `docs:`, `chore:`, or `style:` only.

**GitHub Actions secrets required:**

| Secret | Value |
|---|---|
| `TS_OAUTH_CLIENT_ID` | Tailscale OAuth client ID |
| `TS_OAUTH_SECRET` | Tailscale OAuth secret |
| `NAS_HOST` | NAS Tailscale IP (e.g. `100.x.x.x`) |
| `WEBHOOK_SECRET` | Shared secret for webhook signature verification |

---

## Collaboration & Branch Strategy

### Branch structure

```
main              ← production, protected
development       ← integration branch
feature/*         ← topic branches
```

### Workflow

```bash
# Start a new feature
git checkout development
git checkout -b feature/my-feature

# Work, commit using conventional commits
git commit -m "feat: add kombucha pH tracking"
git commit -m "fix: correct dashboard date comparison"
git commit -m "docs: update README"

# Push and open PR to development
git push origin feature/my-feature
# → CI runs: lint + tests

# After review, merge to development
# When ready to release, open PR from development to main
# → CI runs again, then on merge:
#   → deploy to NAS automatically
#   → release tag created automatically
```

### Commit message convention

Follows [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Effect | Example |
|---|---|---|
| `feat:` | Minor version bump | `feat: add batch export` |
| `fix:` | Patch version bump | `fix: dashboard timezone issue` |
| `feat!:` | Major version bump | `feat!: redesign data model` |
| `chore:` | No bump, no deploy | `chore: update dependencies` |
| `docs:` | No bump, no deploy | `docs: update README` |
| `style:` | No bump, no deploy | `style: fix button spacing` |
| `ci:` | No bump, no deploy | `ci: add lint step` |
| `refactor:` | No bump, no deploy | `refactor: simplify router` |

### Branch protection

- `main` — requires PR, 1 approval, all status checks must pass
- `development` — requires status checks to pass

---

## Database

SQLite database at `ferm.db` (local) or `/volume1/docker/fermlog/ferm.db` (NAS).

**Backup:**
```bash
make backup   # local — saves to backups/
```

On the NAS, automated nightly backups run via cron at 2am:
```
0 2 * * * /volume1/docker/fermlog/backup.sh >> /volume1/docker/fermlog/logs/backup.log 2>&1
```

---

## License

Private — personal use only.