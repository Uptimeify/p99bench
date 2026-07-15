---
name: Submit a result
about: Report benchmark results if you cannot open a pull request
title: "result: <provider> <product> <region>"
labels: result
---

<!--
A pull request is strongly preferred - CI validates it automatically and your
name ends up on the commit. See CONTRIBUTING.md. Use this form only if opening
a PR is genuinely not an option for you.
-->

## The machine

**Provider / product / region**
<!-- e.g. hetzner / CPX41 / fsn1 - the exact instance type as billed -->

**Price and billing model**
<!-- Required. e.g. 29.90 EUR/month, monthly. A result without a price cannot
     be compared against anything. -->

**Boot volume or dedicated data volume?**
<!-- These behave completely differently. If it was a separate block storage
     volume, name the tier (e.g. "Premium SSD v2", "high-speed-nvme"). -->

## The runs

**How many runs, and at what local times?**
<!-- One run is a data point, not a result. Noisy neighbours have schedules.
     Three runs at meaningfully different hours is the minimum before anything
     gets aggregated in RESULTS.md. -->

**Did the sustained test run?**
<!-- i.e. you did NOT pass --skip-steady. Without disk.steady_state a result is
     rejected: there is no way to tell a fast disk from a burst credit balance. -->

**Was the machine otherwise idle?**
<!-- run-all.sh refuses above load 0.5. Did you override it? -->

## The data

**Attach the JSON files**
<!-- Drag them into this issue. One file per run. Do not paste them inline.
     Attach the .log files only if something looks odd and needs explaining. -->

## Disclosure

**Any affiliation with this provider?**
<!-- Provider employees are explicitly welcome to submit results from their own
     hardware - that is a working feedback loop, not a conflict. Undisclosed
     affiliation is the only problem. -->