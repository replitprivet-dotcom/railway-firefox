# railway-firefox

Railway-compatible Firefox web gateway using the exact image `lscr.io/linuxserver/firefox:latest`. The image is pulled automatically by Railway from the `FROM` line in the Dockerfile. This project does not use a VPS, external Firefox installation, or Docker-in-Docker.

## Important architecture limitation

This is a **single Railway service and a single LinuxServer Firefox container**. The LinuxServer image starts one Firefox/noVNC GUI process and one `/config` profile. The gateway gives each user a cryptographically random, revocable endpoint and protects it with an API lookup, but it does **not** create a separate Firefox process or profile per user. Therefore users share the same underlying browser GUI. Endpoint isolation prevents unauthorized URL access; it cannot provide process-level browser isolation.

True per-user Firefox isolation requires one Railway service/container per user (or an external orchestrator that creates Railway services). This repository intentionally does not pretend that Docker-in-Docker or per-user processes exist inside one Railway container.

## Files

| File | Purpose |
|---|---|
| `Dockerfile` | Starts from the required LinuxServer Firefox image and adds Python, Gunicorn, and Nginx. |
| `app.py` | Flask API, SQLite schema, password hashing, endpoint generation, and auth check. |
| `start.sh` | Starts the API and Nginx gateway while the inherited LinuxServer `/init` keeps Firefox/s6 running. |
| `nginx.conf.template` | Railway `$PORT` listener, path stripping, HTTP proxying, and WebSocket/noVNC support. |
| `custom-cont-init.d/10-railway-data` | Creates the persistent data directory. |
| `custom-services.d/railway-gateway/run` | Registers the gateway as an inherited s6 service. |

## Railway deployment

1. Create a new **private** GitHub repository named `railway-firefox` and push this repository, or deploy the repository directly from GitHub in Railway.
2. In Railway, select **New Project → Deploy from GitHub Repo**, then choose `railway-firefox`.
3. Add a Railway Volume and mount it at **`/data`**. SQLite and the generated database will be stored at `/data/users.db`.
4. Add these variables in the service:

   - `ADMIN_API_KEY`: long random administrator key. Do not use the example key from the original prompt.
   - `SECRET_KEY`: long random persistent Flask secret. Keep it unchanged across redeploys.
   - `TZ`: for example `Asia/Kolkata`.
   - `PUID` / `PGID`: normally `0` for the LinuxServer base image unless you have verified another UID works with its startup scripts.
   - `CUSTOM_USER` / `PASSWORD`: required by the LinuxServer Firefox image. Use a strong random value; it is the image's internal fallback login, not the generated endpoint authorization.
   - `PUBLIC_BASE_URL`: optional, for example `https://your-service.up.railway.app`. Set it if Railway's detected host is not the URL you want returned by `/adduser`.

5. Railway supplies `PORT` automatically. Do not manually expose port `3000` or `3001`; Nginx listens on Railway's `$PORT` and proxies internally to the LinuxServer GUI on `127.0.0.1:3000`.
6. Deploy and wait for the build to finish. The image `lscr.io/linuxserver/firefox:latest` will be pulled during the Docker build.

Generate secrets locally with:

```bash
openssl rand -base64 32
```

## API usage

Replace the placeholders below. URL-encode query values in real scripts.

```bash
BASE='https://your-service.up.railway.app'
KEY='your-long-admin-api-key'

curl -i "$BASE/"

curl -iG "$BASE/adduser" \
  --data-urlencode "key=$KEY" \
  --data-urlencode 'username=test' \
  --data-urlencode 'password=Use-a-strong-password-123!'

curl -iG "$BASE/delete" \
  --data-urlencode "key=$KEY" \
  --data-urlencode 'username=test' \
  --data-urlencode 'password=Use-a-strong-password-123!'
```

A successful create response looks like:

```json
{
  "status": "created",
  "username": "test",
  "endpoint": "/firefox/<random-id>",
  "url": "https://your-service.up.railway.app/firefox/<random-id>/"
}
```

Open the returned `url` in a browser. Nginx authenticates the random endpoint, strips the endpoint prefix before forwarding to LinuxServer's GUI, and forwards HTTP Upgrade headers for noVNC WebSockets. A revoked endpoint returns `404` and cannot access the GUI.

## Verification checklist

- `GET /` returns the gateway landing page.
- Invalid or missing `key` on `/adduser` and `/delete` returns `401`.
- Passwords are stored only as Werkzeug password hashes in SQLite.
- Endpoint IDs use `secrets.token_urlsafe` and are unique in the database.
- `/data/users.db` survives redeploys when the Railway Volume remains attached.
- `nginx.conf.template` uses `proxy_http_version 1.1`, `Upgrade`, `Connection`, long read timeouts, and disabled buffering for noVNC.
- The endpoint URL must be tested in a real browser: confirm that the Firefox HTML/JavaScript loads, the canvas appears, and the browser developer console shows a successful WebSocket connection. A plain `curl` can verify the HTTP status but cannot verify a GUI WebSocket.

## Local smoke test

A full local run needs Docker because the Firefox base image is intentionally required:

```bash
docker build -t railway-firefox .
docker run --rm -p 8080:8080 \
  -e PORT=8080 \
  -e ADMIN_API_KEY='local-long-key' \
  -e SECRET_KEY='local-long-secret' \
  -e CUSTOM_USER=admin \
  -e PASSWORD='local-image-password' \
  -v "$PWD/data:/data" \
  railway-firefox
```

Then use `http://localhost:8080/` and the same curl commands with `BASE=http://localhost:8080`.

## Security notes

Do not commit secrets. The administrator API is intentionally query-parameter based to preserve the requested API shape; use HTTPS (Railway provides TLS), keep the service private where possible, use long random keys, and avoid putting `/adduser` URLs in shell history or public logs. For sensitive workloads, use one Railway service and volume per browser user rather than this shared-process design.
