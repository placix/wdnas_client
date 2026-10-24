# API Documentation

This document outlines the API endpoints for the WD NAS systems supported by this module.

## Version 2 Systems (V2)

| Function | HTTP Method | Endpoint | Parameters / Body | Notes |
| --- | --- | --- | --- | --- |
| Login | `POST` | `/cgi-bin/login_mgr.cgi` | `cmd=wd_login&username=user1&pwd=alisdh2309oas` | `application/x-www-form-urlencoded` body |
| System Info | `GET` | `/xml/sysinfo.xml` | *None* | |
| Share Names | `POST` | `/web/get_share_name_list.php` | *None* | |
| System Status | `POST` | `/cgi-bin/status_mgr.cgi` | `cmd=resource` | `application/x-www-form-urlencoded` body |
| Network Info | `GET` | `/cgi-bin/network_mgr.cgi` | `cmd=cgi_get_lan_xml` | Query Parameter |
| Device Info | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=cgi_get_device_info` | `application/x-www-form-urlencoded` body |
| System Version | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=get_firm_v_xml` | `application/x-www-form-urlencoded` body |
| Latest Version | `POST` | `/cgi-bin/system_mgr.cgi` | `get_auto_fw_version` | `application/x-www-form-urlencoded` body |
| Accounts | `POST` | `/xml/account.xml` | *None* | |
| Alerts | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=cgi_get_alert` | `application/x-www-form-urlencoded` body |

## Version 5 Systems (V5)

| Function | HTTP Method | Endpoint | Parameters / Body | Notes |
| --- | --- | --- | --- | --- |
| Login | `POST` | `/nas/v1/auth` | `{"username":"user1","password":"alisdh2309oas"}` | `application/json` body |
| System Info | `GET` | `/xml/sysinfo.xml` | *None* | |
| Share Names | `POST` | `/web/get_share_name_list.php` | *None* | |
| System Status | `POST` | `/cgi-bin/status_mgr.cgi` | `cmd=resource` | `application/x-www-form-urlencoded` body |
| Network Info | `GET` | `/cgi-bin/network_mgr.cgi` | `cmd=cgi_get_lan_xml` | Query Parameter |
| Device Info | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=cgi_get_device_info` | `application/x-www-form-urlencoded` body |
| System Version | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=get_firm_v_xml` | `application/x-www-form-urlencoded` body |
| Accounts | `POST` | `/xml/account.xml` | *None* | |
| Alerts | `POST` | `/cgi-bin/system_mgr.cgi` | `cmd=cgi_get_alert` | `application/x-www-form-urlencoded` body |
| Cloud Access | `POST` | `/web/restSDK/cloudAccess.php` | `cmd=getCloudAccess` | `application/x-www-form-urlencoded` body |
| USB Info | `GET` | `/web/get_usb_info.php` | *None* | `success: false` can mean that no USB device is attached |
| Diagnostics | `POST` | `/cgi-bin/home_mgr.cgi` | `cmd=2` | Disk, fan, board, and RAID data |
| Capacity Usage | `POST` | `/cgi-bin/hd_config.cgi` | `cmd=cgi_Status_CapacityUsage` | Per-volume raw KiB counters and percentage |
| Uptime | `POST` | `/cgi-bin/status_mgr.cgi` | `cmd=cgi_get_uptime` | Not available on all OS 5 models |

## Method responses and compatibility

### `await nas.diagnostics()`

OS 5 only. The method returns a dictionary with this shape:

```python
{
    "board_temperature": 0,
    "fan_rpm": 0,
    "disk_statuses": ["healthy", "healthy"],
    "disk_status_extra": {},
    "raid": {
        "volume_count": 1,
        "result": 1,
        "disk_count": 2,
        "volumes": [
            {
                "id": "1",
                "number": 1,
                "volume": "Volume_1",
                "mode": "raid1",
                "state": "clean",
                "extra": {},
            }
        ],
        "extra": {},
    },
    "extra": {},
}
```

`board_temperature` is a WD diagnostic code, not a Celsius temperature.
`fan_rpm` is the RPM value reported by the firmware; zero is not interpreted as
healthy or unhealthy. RAID states are returned verbatim. Known values include
`clean`, `degraded`, `damaged`, `resync`, `recovery`, `migrate`, `reshape`, and
`expansion`. Missing known elements become `None` (or an empty list), while
additional XML values are retained in the nearest `extra` mapping.

The WD My Cloud Mirror response shown above was verified on real firmware
5.33.102. Failure/rebuild states, missing fields, unknown fields, and multiple
RAID volumes are verified with automated response fixtures.

### `await nas.capacity_usage()`

OS 5 only. Returns a list so that devices with multiple volumes are represented
without loss:

```python
[
    {
        "volume": 1,
        "total_size": 1918590988,
        "used_size": 1918589500,
        "free_size": 1488,
        "used_rate": 100,
        "shared_pictures": 0,
        "shared_music": 0,
        "shared_videos": 0,
        "extra": {},
    }
]
```

The WD web-interface code treats `total_size`, `use_size`, `free_size`, and the
three media-category values as KiB counters. The client returns those integers
unchanged; it does not convert or reconcile them with `system_info()`, whose
endpoint may report different storage figures. `used_rate` is an integer
percentage. Missing values become `None`, and additional item fields are kept in
`extra`. One-volume behavior was verified on real Mirror firmware 5.33.102;
multi-volume parsing is fixture-tested.

### `await nas.usb_info()`

OS 5 only. This uses the same `GET` request as the original WD web interface.
The JSON response is returned unchanged. In particular, `{"success": false}`
without an attached USB device is not converted into a communication error.

### `await nas.uptime()`

OS 5 only. Existing successful uptime parsing remains available. If the endpoint
returns the observed empty HTTP 500 response or no supported `<uptime>` value,
the method raises `UptimeUnavailableError`. This exception subclasses
`RequestFailedError` for backward-compatible exception handling. Other HTTP
errors remain `RequestFailedError`, while transport errors continue to propagate
from `aiohttp`. It never derives uptime from `resource`'s `now_time` value. WD My
Cloud Mirror firmware 5.33.102 returns an empty HTTP 500 response for both
`cgi_get_uptime` and `cgi_get_general`; uptime therefore remains unavailable on
that tested device. The client has no cached model/firmware identity at this
point, so it deliberately does not attempt speculative model detection.
