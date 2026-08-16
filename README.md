# SignalLoom InvoiceOps

SignalLoom InvoiceOps is a local-first, human-gated Windows workflow agent for invoice extraction, review, exception handling, approval preparation, and AP handoff artifacts.

**Current public release:** v1.0.1  
**Product page:** https://salt19.com/SignalLoom/  
**Release:** https://github.com/GabrielAllit1/SignalLoom/releases/tag/v1.0.1

## Download

Normal Windows users should use the signed/released installer attached to the latest GitHub release:

https://github.com/GabrielAllit1/SignalLoom/releases/download/v1.0.1/SignalLoomOps_Setup.exe

SHA-256 published by GitHub for the v1.0.1 installer:

`8f63f5fd80b1542b509e90adc6987615cbf3edeef53a200b03e1e8c84937ad57`

## What SignalLoom does

SignalLoom converts invoice and office-document inputs into structured review outputs while keeping consequential routing human-gated. The v1.0.1 bounded InvoiceOps runtime supports:

- Source-faithful invoice and workbook extraction
- Invoice review and exception detection
- Agent run reports
- AP review packets
- Approval-route recommendations
- Slack approval-message preparation
- Exception checklists
- n8n / Make / Zapier-ready handoff payloads
- Local Qwen/Ollama-assisted analysis and revision
- Observe → plan → act → verify workflow stages

## Governance boundary

SignalLoom is intentionally bounded. The public v1.0.1 release is designed to prepare review and handoff artifacts rather than autonomously approve or transmit consequential financial actions.

The release specifically preserves human gating and blocks:

- Automatic payment approval
- Automatic webhook sending
- Modification of source accounting records

## Local-first operation

SignalLoom is designed to keep invoice review and working artifacts close to the user's machine. Local Qwen/Ollama support is available for supported analysis workflows rather than requiring invoice content to be sent to a SALT19-hosted model service.

## Repository scope

This repository is the canonical public GitHub release location for SignalLoom InvoiceOps. The downloadable Windows installer and release notes are available under **Releases**. Product documentation and screenshots are also published on the SALT19 product page.

## Publisher

SALT19 LLC  
https://salt19.com/  
info@salt19.com
