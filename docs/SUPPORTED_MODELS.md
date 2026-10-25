# Supported Devices Documentation

<!-- 
&#10003; for supported
- for not sure
&#10005; for not supported
-->

This document outlines the supported WD NAS systems.

## Version 2 Systems (V2)

| Model | Firmware Version | Supported |
| --- | --- | --- |
| WD My Cloud Mirror | 2.13.108 | &#10003; |

## Version 5 Systems (V5)

| Model | Firmware Version | Supported | Validation |
| --- | --- | --- | --- |
| My Cloud Home | 5.31.108 | - | Existing project listing; not verified by this change |
| My Cloud EX2 Ultra | 5.31.108 | &#10003; | Existing project listing; not re-tested by this change |
| WD My Cloud PR2100 | 5.31.108 | - | Existing project listing; not verified by this change |
| WD My Cloud Mirror | 5.33.102 | &#10003; (uptime unavailable) | Tested on real hardware |

For the WD My Cloud Mirror, login, `system_version()`, `device_info()`,
`system_info()`, `system_status()`, `network_info()`, `share_names()`,
`accounts()`, `alerts()`, `diagnostics()`, `capacity_usage()`, `volume_status()`,
`system_warning()`, `internet_access()`, `process_info()`, and the extended
`system_status()` response were tested on the physical device. `usb_info()` was
tested without a connected USB device and
returned `{"success": false}`. The documented RAID failure/rebuild variants and
multi-volume parsing are covered by automated fixtures rather than live fault
injection. The device returned HTTP 500 with an empty body for `uptime()` and
HTTP 404 for the state-dependent `/xml/dm_read_state.xml` resource. Compatibility
of the new monitoring methods is asserted only for Mirror firmware 5.33.102;
other listed OS 5 models retain their existing support status.

The Mirror dashboard source for firmware 5.33.102 confirms the safety-check,
restart, and shutdown CGI commands implemented by `reboot()` and `shutdown()`.
Only automated response fixtures were used for these destructive actions; they
were deliberately not executed against the physical NAS.

## Version 4 Systems (V4 identifier)

V4 is this integration's identifier for original WD My Cloud devices in the OS
3 family; it is not a claim that WD released a product named My Cloud OS 4.

| Model | Model number | Firmware | Support status | Validation |
| --- | --- | --- | --- | --- |
| WD My Cloud | `sq` | `04.06.00-111` | Provisional | Read endpoints manually verified in an authenticated browser; programmatic login and all parsing covered by offline mocks; independent hardware login still pending |

The verified browser endpoints are `/api/2.1/rest/system_information`,
`/api/2.1/rest/firmware_info`, and `/api/2.1/rest/system_state`. The first
provides identity and the `2TB` capacity label; the other resources provide
firmware package data and qualitative health strings. No CPU, memory, used/free
byte counters, numeric temperature, detailed disk list, or detailed volume list
has been established, so the client does not fabricate them.

The login endpoint is `/api/2.1/rest/local_login` with username and password
query parameters. It is implemented and mock-tested, but physical-device
authentication remains to be validated. HTTP does not encrypt these query
parameters. No V4 server-side logout or power endpoint is enabled without the
missing original JavaScript evidence.
