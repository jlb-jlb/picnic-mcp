# Security policy

This server holds a login to a real Picnic account, so security reports are taken seriously.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on this repository (Security tab, "Report a vulnerability"), or write to picnic-mcp@joschabisping.de. Include what you found, the steps to reproduce it and the version. You will get an answer within a few days.

## What is in scope

- Anything that exposes the password, the one-time code or the session token, including the local sign-in page and the credential store backends.
- Anything that lets another website or another local user reach the sign-in page.
- Anything that makes the server contact a host other than the Picnic API.

## What is not in scope

- Changes or blocks on Picnic's side. The project uses Picnic's private API without permission and says so in the README.
- A compromised computer or a compromised operating system credential store.

## Supported versions

Only the latest release receives fixes.

## How the project protects itself

- No third-party dependencies, and the lockfile is enforced with `uv run --locked`.
- The CI actions are pinned to commit hashes and updated through Dependabot pull requests.
- Releases are tagged, tested and checked for matching version numbers before they are published.
