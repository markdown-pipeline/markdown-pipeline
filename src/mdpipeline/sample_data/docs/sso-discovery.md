---
sample: true
---
# Single sign-on: discovery notes

Epic: ACME-6. Status: waiting for customer interviews.

## What we know

- Four of the six larger prospects asked for SAML in the last quarter.
- Two of them said they would pay for the Team plan only if it includes SSO.
- Nobody asked for SCIM provisioning yet.

## Options

| Option | Effort | Risk |
| --- | --- | --- |
| Build SAML ourselves | 6-8 weeks | Security review needed |
| Use an identity provider service | 2-3 weeks | Monthly cost per user |

## Open decisions

- Build or buy. We lean towards buying, to ship in Sprint 26.
- Whether SSO is a Team plan feature or an add-on.
