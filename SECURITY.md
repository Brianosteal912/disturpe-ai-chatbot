# Security Policy

## Reporting a vulnerability

Please do not disclose exploitable details, credentials, private messages, or personal data in a public issue.

Use GitHub's **Security > Advisories > Report a vulnerability** flow for this repository. Include the affected version, impact, minimal reproduction steps, and any suggested mitigation. Remove real tokens and user data from screenshots or logs.

If private vulnerability reporting is not enabled, open a public issue containing only a request for a private contact channel and no vulnerability details.

## Deployment responsibilities

Operators must keep `.env` and `data/` out of Git, rotate exposed credentials, restrict Discord log channels, use HTTPS for remote AI APIs, apply dependency updates, and protect host backups. Review the configured AI provider's data handling before processing user messages.