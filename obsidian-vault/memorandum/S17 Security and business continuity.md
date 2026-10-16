---
category: cybersecurity
date: 2026-09-30
next_review: 2027-09-30
owner: COO and Head of IT
reference: S17
section: 17  Security and business continuity
type: source
---

The technology model uses managed applications for orders, accounting and documents, with named user accounts and segregation by function. The controls reported are invented features of the case and do not describe Alken’s actual infrastructure.

| Area | Planned control | Frequency or measure |
| --- | --- | --- |
| Identity | Multi-factor authentication for remote and privileged access | Quarterly access review; revocation within four hours of departures |
| Data | Encryption in transit and at rest; separate investor archives | Least-privilege access and centralised logs |
| Endpoints | Managed protection and blocking of unauthorised installations | Continuous monitoring; critical updates within 14 days |
| Backups | Daily copies and incremental replication every four hours | Immutable backups for 35 days; quarterly recovery tests |
| Vulnerabilities | System scans and external penetration testing | Monthly scans; annual external test |
| Personnel | Training and phishing simulations | Annual training; quarterly campaigns |
| Critical services | Alternative channel with the administrator and prime brokers | Full exercise at least annually |

## Recovery objectives

The recovery time objective for critical trading and reconciliation processes is eight hours; the recovery point objective for the document archive is four hours. The first is the intended time to restore service; the second is the maximum planned data-loss window. These are plan objectives, not continuity guarantees.

The simulated test on 15 May 2026 restored access to critical systems in six hours and recovered a data point from two hours before the interruption. The simulated penetration test on 20 June 2026 identified two medium and three low vulnerabilities, remediated by 31 July; no critical vulnerability is assumed. The technical report and independent certification are not included.

## Incident management

The service desk informs IT and the COO within one hour of confirming a serious incident. The crisis group comprises the COO, IT, CCO and affected providers. Response includes isolation, preservation of evidence, assessment of affected data, recovery and root cause analysis. Compliance determines notification recipients and timing case by case, without assuming a single legal deadline.

In the scenario, a 45-minute document portal outage on 8 July 2026 did not affect trading or cause data loss. No other material cybersecurity breaches are assumed between inception and the memorandum date. This absence is a narrative premise, not the result of an actual audit.

## Residual risks

The plan retains dependencies on cloud services, telecommunications, the administrator and clearing. An event affecting several providers may exceed planned recovery times. If pre trade controls are unavailable, only risk reductions approved by the CRO through an alternative procedure are permitted; new investments await restoration.