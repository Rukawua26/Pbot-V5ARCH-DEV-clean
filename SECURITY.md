# Security Policy

## Supported Scope

This repository contains a trading bot and operational scripts. Security issues are treated as high priority when they can:

- leak credentials or account data
- execute unauthorized orders
- bypass risk controls
- expose sensitive telemetry

## Reporting a Vulnerability

Please report privately and do not open a public issue for active vulnerabilities.

Recommended report content:

- affected file/module
- impact and exploitability
- reproduction steps
- suggested mitigation

## Secrets Handling

Never commit secrets:

- `.env`
- API keys / tokens
- private keys

Use environment variables and local secret stores.

## Hardening Guidelines

- Keep branch protection enabled on the default production branch (`master` currently).
- Require PR review for production-facing changes.
- Avoid force pushes to protected branches.
- Validate risk limits after config changes.

## Operational Safety

Before deploying:

1. Confirm exchange keys are correct and least-privileged.
2. Run in shadow mode after major strategy changes.
3. Check telemetry consistency (terminal vs Telegram vs DB).
4. Verify kill-switch and stop commands.
