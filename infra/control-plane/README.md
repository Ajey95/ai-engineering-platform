# Trusted control-plane image

`Dockerfile` packages the locked Python application, migrations and operator
scripts as a non-root container. It is for the API and trusted control workers.
It does not install a browser or execute a customer repository; the
development-only fixture runner remains a different image.

Build with Docker Engine:

```powershell
wsl -d Ubuntu-24.04 -u root -- docker build -f /mnt/d/projects/aiplatform/infra/control-plane/Dockerfile -t aip-control-plane:0.1.1 /mnt/d/projects/aiplatform
```

The API defaults to port 8000. A hosted task must inject a migrated PostgreSQL
URL, validated OIDC settings, the public HTTPS base URL and scoped secret
references, and use a workload role. Run `alembic upgrade head` as a separate
controlled migration task before API rollout. The image by itself is not a
hosted release: customer-run admission still fails closed until dedicated VM
isolation, private artifact publication, queue workers, live identity and
restore qualification are implemented.
