# railway-firefox

Railway-hosted multi-user Firefox service. The gateway uses the exact image `lscr.io/linuxserver/firefox:latest` for every provisioned user. It does not use a VPS, external Firefox installation, or Docker-in-Docker.

## How true multi-user isolation works

The gateway is deployed once. Every successful `/adduser` call uses the Railway GraphQL API to create a **separate Railway service** from the LinuxServer Firefox image, a separate `/config` Volume, and a separate Railway HTTPS domain. The generated random endpoint is authenticated by the gateway and reverse-proxied to that user's service, including WebSocket/noVNC traffic. Each user therefore gets a separate Firefox process and profile. `/delete` deletes the user's Railway service and revokes the endpoint.

This architecture consumes Railway resources per active user. Do not provision untrusted users without rate limiting and billing controls.

## Files

- `Dockerfile`: starts from `lscr.io/linuxserver/firefox:latest` and adds Python, Gunicorn, and Nginx.
- `app.py`: API, hashed credentials, SQLite records, and Railway service/volume/domain provisioning.
- `start.sh`: runs the API and Nginx while inherited LinuxServer `/init` starts Firefox.
- `nginx.conf.template`: dynamic per-user HTTPS proxy with WebSocket support.
- `railway.toml`: Docker build and health-check configuration.

## Deploy gateway to Railway

1. Deploy this repository as the gateway service.
2. Add a persistent Railway Volume mounted at `/data`.
3. Set these gateway variables:

```text
ADMIN_API_KEY=<long random admin key>
SECRET_KEY=<long random persistent Flask key>
RAILWAY_API_TOKEN=<Railway account/workspace token with project-management access>
RAILWAY_PROJECT_ID=<gateway Railway project ID>
RAILWAY_ENVIRONMENT_ID=<gateway production environment ID>
PUBLIC_BASE_URL=https://<gateway-domain>
TZ=Asia/Kolkata
```

`RAILWAY_API_TOKEN` is used only at runtime by `/adduser` and `/delete`; never commit it to GitHub. The gateway project's token must be allowed to create services, volumes, domains, and delete services in that project. `CUSTOM_USER` and `PASSWORD` are not needed on the gateway; they are generated/provided per user for the child Firefox service.

The gateway listens on Railway's automatically supplied `$PORT`. Do not expose child ports directly; each child domain targets internal port `3000` through Railway's service networking.

## API

```bash
BASE='https://<gateway-domain>'
KEY='<admin-key>'

curl -i "$BASE/"
curl -i "$BASE/health"

curl -iG "$BASE/adduser" \
  --data-urlencode "key=$KEY" \
  --data-urlencode 'username=test' \
  --data-urlencode 'password=Use-a-strong-password-123!'

curl -iG "$BASE/delete" \
  --data-urlencode "key=$KEY" \
  --data-urlencode 'username=test' \
  --data-urlencode 'password=Use-a-strong-password-123!'
```

The create response includes `endpoint`, `url`, and `isolated_service`. Open `url` in a browser. The gateway authenticates the random endpoint, strips its prefix, proxies to the user's child service, and forwards HTTP Upgrade headers. Verify the Firefox canvas and a successful WebSocket in browser developer tools. A revoked endpoint returns `404`.

## Security

Passwords are stored only as Werkzeug hashes in the gateway's SQLite database at `/data/users.db`; the child service receives its own password as a Railway variable so LinuxServer's built-in web authentication can start correctly. Use HTTPS, long random keys, a persistent `SECRET_KEY`, and a dedicated Railway token with the smallest practical scope. Because the requested API shape uses query parameters, avoid putting `/adduser` and `/delete` URLs into shared logs or shell history.

## Local checks

```bash
python3 -m py_compile app.py
git status
```

A full local GUI test requires Docker and Railway API credentials. The Railway API is intentionally not mocked in production code: a failed child-service provisioning call returns HTTP `502` and does not create a gateway user record.
