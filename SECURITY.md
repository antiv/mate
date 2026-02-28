# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in MATE, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email security concerns to the maintainers or use [GitHub's private vulnerability reporting](https://github.com/antiv/mate/security/advisories/new).

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 1 week
- **Fix timeline**: Depends on severity, typically within 2 weeks for critical issues

## Security Best Practices for Deployment

### Authentication

- **Change default credentials**: Set `AUTH_USERNAME` and `AUTH_PASSWORD` environment variables. Never use the defaults in production.
- **Use HTTPS**: Deploy behind a reverse proxy (nginx, Caddy) with TLS termination.
- **Restrict CORS**: Configure `allow_origins` to your specific domains instead of `["*"]`.

### API Keys

- Store all API keys in environment variables, never in code or config files.
- Use separate API keys for development and production.
- Rotate keys periodically.

### Database

- Use PostgreSQL or MySQL for production (not SQLite).
- Use strong database passwords.
- Enable connection encryption (SSL/TLS) for remote database connections.
- Run database migrations only through the built-in migration system.

### Network

- Do not expose the ADK server (port 8001) directly. All requests should go through the auth server (port 8000).
- Use firewall rules to restrict access to necessary ports only.
- Consider running behind a VPN for internal deployments.

### Docker

- Do not hardcode secrets in `docker-compose.yml`. Use `.env` files or secret management.
- Run containers with minimal privileges.
- Keep base images updated.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | Yes                |

## Scope

The following are in scope for security reports:

- Authentication and authorization bypass
- SQL injection or other injection attacks
- Credential or token exposure
- Privilege escalation
- Cross-site scripting (XSS) in the dashboard
- Insecure defaults that could lead to compromise

The following are **out of scope**:

- Issues in third-party dependencies (report to those projects directly)
- Denial of service attacks
- Social engineering
