# Post-Mortem: PostgreSQL "password authentication failed" on Windows

**Date:** 2026-07-25
**Component:** Database layer (PostgreSQL in Docker + SQLAlchemy)
**Severity:** Blocking — the app could not connect to its database
**Status:** Resolved

---

## Summary

After migrating Echo's storage from SQLite to PostgreSQL (running in Docker),
the application could not connect to the database. Every attempt — from
`run.py`, from the API, and from pgAdmin — failed with:

```
FATAL: password authentication failed for user "echo"
```

The root cause was **not** wrong credentials or misconfiguration in the app.
It was a **port conflict**: two native Windows PostgreSQL installations were
already listening on the ports Docker tried to publish, so connections from the
host were being answered by the wrong PostgreSQL server — one that had no `echo`
user.

---

## Symptom

The error was consistent and misleading:

```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError)
connection to server at "localhost" (::1), port 5432 failed:
FATAL: password authentication failed for user "echo"
```

It *looked* like a credentials problem, which sent troubleshooting down several
dead ends before the real cause was found.

---

## What made it confusing

A key contradiction eventually cracked the case:

| Test | Result |
|------|--------|
| `psql` **inside** the container (`docker compose exec postgres psql -U echo`) | ✅ Success — returned `1` |
| `psql` from the **host**, via the published port (`host.docker.internal:5433`) | ❌ Auth failed for `echo` |

Same user, same password, same database — yet connecting *inside* the container
worked while connecting *through the published port* failed. That could only
mean one thing: **the server answering on the published port was not the Echo
container.** Something else was intercepting it.

---

## Root cause

Diagnosing the ports revealed the truth:

```powershell
netstat -ano | Select-String ":5432"
```
```
TCP  0.0.0.0:5432  LISTENING  7192     <- native postgres.exe
TCP  0.0.0.0:5432  LISTENING  38152    <- com.docker.backend (the container)
```

Two processes were fighting over port 5432. And:

```powershell
Get-Service | Where-Object { $_.Name -like "*postgres*" }
```
```
Running  postgresql-x64-17   PostgreSQL Server 17
Running  postgresql-x64-18   PostgreSQL Server 18
```

**Two native Windows PostgreSQL services** (versions 17 and 18) were installed
and running. One of them had claimed port 5432 before Docker, so when the app
connected to `127.0.0.1:5432`, it reached the *native* PostgreSQL — which has no
`echo` user — instead of the container. Hence the auth failure.

Remapping the container to port 5433 didn't help at first, because a native
service was also occupying 5433.

---

## Contributing factors

Several secondary issues added noise and slowed diagnosis:

1. **IPv6 vs IPv4 resolution.** On Windows, `localhost` resolves to IPv6 `::1`
   first. The error showed `"localhost" (::1)`, and the IPv6 path behaved
   differently from IPv4. Switching the connection string to `127.0.0.1` forced
   IPv4 and removed this variable.

2. **PostgreSQL bakes credentials into its data volume on first init.**
   `POSTGRES_USER` / `POSTGRES_PASSWORD` are only applied when the data
   directory is empty. Editing `.env` and restarting does nothing if the volume
   already exists with old credentials. A `docker compose down -v` (note the
   `-v`) is required to wipe the volume and force re-initialization.

3. **`DATABASE_URL` overrides the individual `POSTGRES_*` parts.** The config is
   built so a full `DATABASE_URL`, if present, wins. During troubleshooting the
   URL still contained `localhost` and an old port even after the individual
   parts were updated, masking progress.

4. **`down -v` also wipes the Ollama model volume.** Using `-v` to reset
   Postgres deleted the pulled `qwen2.5:3b` model too, surfacing a later
   (unrelated) `404` from Ollama that had to be fixed by re-pulling the model.

---

## Resolution

1. **Stop the native PostgreSQL services** (in an *Administrator* PowerShell):

   ```powershell
   Stop-Service postgresql-x64-17
   Stop-Service postgresql-x64-18
   Set-Service  postgresql-x64-17 -StartupType Manual
   Set-Service  postgresql-x64-18 -StartupType Manual
   ```

   > Note: the first attempt failed with "Access is denied" because the shell
   > was not elevated. The commands must be run in a PowerShell launched via
   > **Run as administrator**.

2. **Confirm the port is now owned only by the container:**

   ```powershell
   netstat -ano | Select-String ":5433"
   # -> only com.docker.backend (PID 38152), nothing native
   ```

3. **Use `127.0.0.1` (not `localhost`) in the connection string** to force IPv4:

   ```
   DATABASE_URL=postgresql+psycopg2://echo:echo_pass@127.0.0.1:5433/echo_db
   ```

4. **Verify from the host** (the test that had been failing):

   ```powershell
   docker run --rm postgres:16-alpine \
     psql "postgresql://echo:echo_pass@host.docker.internal:5433/echo_db" -c "SELECT 1;"
   # -> returns 1
   ```

5. The app then connected successfully.

---

## How to diagnose this class of problem quickly

If you ever see "password authentication failed" but you're *sure* the
credentials are right, run this sequence before touching config:

```powershell
# 1. Who is actually listening on the DB port?
netstat -ano | Select-String ":5432"

# 2. What processes are those PIDs?
Get-Process -Id (netstat -ano | Select-String ":5432" |
  ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique)

# 3. Any native Postgres services competing?
Get-Service | Where-Object { $_.Name -like "*postgres*" }

# 4. Does auth work INSIDE the container? (isolates container vs. host)
docker compose exec postgres psql -U echo -d echo_db -c "SELECT 1;"

# 5. Does auth work from the HOST via the published port?
docker run --rm postgres:16-alpine `
  psql "postgresql://echo:echo_pass@host.docker.internal:5433/echo_db" -c "SELECT 1;"
```

**The tell:** if step 4 succeeds but step 5 fails, the host is reaching a
*different* server than the container — a port conflict, not a credentials
problem.

---

## Lessons learned

- "Password authentication failed" does not always mean the password is wrong.
  It can mean you're talking to the wrong server entirely.
- On Windows, pre-existing native PostgreSQL installs are a common, silent
  source of Docker port conflicts. Check `netstat` and `Get-Service` early.
- Prefer `127.0.0.1` over `localhost` for local DB connections on Windows to
  avoid IPv6 ambiguity.
- Remember that `POSTGRES_*` env vars only apply on a fresh volume; use
  `docker compose down -v` to reset — but know it wipes *all* named volumes,
  including model caches.
- The fastest diagnostic is comparing an *inside-the-container* connection with
  a *from-the-host* connection. When they disagree, the problem is the network
  path, not the credentials.

---

## Prevention

- Consider giving the Postgres service a non-default host port from the start
  (e.g. `"5434:5432"`) to avoid clashing with any native install on 5432/5433.
- Document required host ports in the README so conflicts are caught during
  setup rather than at runtime.
