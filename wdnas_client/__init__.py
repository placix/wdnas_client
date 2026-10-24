import aiohttp, json, base64
import re
from datetime import timedelta
from .exceptions import InvalidLoginError, RequestFailedError, UptimeUnavailableError
from .const import V2_RAW_LOGIN_STRING, SCHEME, ENDPOINTS
from xml.etree import ElementTree
import http.cookies


def _optional_int(value):
    """Return an integer for numeric XML text, preserving other values."""
    if value is None:
        return None
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        return value


def _optional_number(value):
    """Return an int/float for numeric XML text, preserving other values."""
    value = _optional_int(value)
    if not isinstance(value, str):
        return value
    try:
        return float(value)
    except ValueError:
        return value


def _xml_value(element):
    """Convert arbitrary XML into JSON-compatible data without dropping fields."""
    children = list(element)
    if not children:
        value = _optional_int(element.text)
    else:
        value = _xml_children(element)

    if element.attrib:
        if not isinstance(value, dict):
            value = {"value": value}
        value["attributes"] = dict(element.attrib)
    return value


def _xml_children(element, excluded=()):
    """Return selected XML children while preserving repeated tag names."""
    result = {}
    for child in element:
        if child.tag in excluded:
            continue
        value = _xml_value(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result

class client:
    """
    A client class for interacting with the WD NAS device API's (Versions 2 or 5).

    :host: The hostname or IP address of the device.
    :username: The username used for authentication (stored in lowercase).
    :password: The password used for authentication.
    :version: The API version being used (2 or 5).
    :session: An aiohttp.ClientSession object for making async requests (initially None).
    :phpsessid: The PHP session ID token (initially None; set after login).
    :wd_csrf_token: The CSRF token required for v2 requests (initially None; set after login).
    """
    def __init__(self, username: str, password: str, host: str, version: int):
        if version not in [2, 5]:
            raise ValueError("Unsupported/invalid version. Must be 2 or 5.")
        
        self.host = host
        self.username = username.lower()
        self.password = password
        self.version = version
        self.session = None
        self.phpsessid = None
        self.wd_csrf_token = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.session.close()
        
    async def login(self) -> None:
        """Login to device"""
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['login']}"

        enc_password = base64.b64encode(self.password.encode('utf-8')).decode("utf-8")

        if self.version == 2:
            data = V2_RAW_LOGIN_STRING.format(username=self.username, enc_password=enc_password)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Host": self.host,
            }
            async with self.session.post(url, data=data, headers=headers) as response:
                pass
        elif self.version == 5:
            json_payload = {
                "username": self.username,
                "password": enc_password
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Host": self.host,
            }
            async with self.session.post(url, json=json_payload, headers=headers) as response:
                pass

        
        if response.status == 200:

            set_cookies = response.headers.getall('Set-Cookie', [])
            for cookie_str in set_cookies:
                cookie = http.cookies.SimpleCookie(cookie_str)
                for key, morsel in cookie.items():
                    self.session.cookie_jar.update_cookies({key: morsel.value})

            cookies = response.cookies

            if self.version == 2:
                if "PHPSESSID" in cookies and "WD-CSRF-TOKEN" in cookies:
                    self.phpsessid = cookies["PHPSESSID"].value
                    self.wd_csrf_token = cookies["WD-CSRF-TOKEN"].value
                else:
                    raise InvalidLoginError("Invalid Username/Password or missing cookies")
            elif self.version == 5:
                if "PHPSESSID" in cookies:
                    self.phpsessid = cookies["PHPSESSID"].value
        else:
            raise RequestFailedError(response.status)
    
    async def system_info(self) -> dict:
        """
        Fetches system information from the device.

        This includes data about physical **disks**, logical **volumes**, and overall
        storage **size** (total, used, and unused).
        """
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_info']}"
        if self.version == 2:     
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {
                "Host": self.host
            }
        else:
            raise ValueError("Unsupported/invalid version.")
            
        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_info = ElementTree.fromstring(content)
                device_info_json = {"disks": [], "volumes": [], "size": {}}
                for disk in device_info.iter('disk'):
                    device_info_json['disks'].append({
                        "id": disk.attrib['id'],
                        "name":  disk.findtext('name'),
                        "connected":  bool(int(disk.findtext('connected'))),
                        "vendor":  disk.findtext('vendor'),
                        "model":  disk.findtext('model'),
                        "rev":  disk.findtext('rev'),
                        "sn":  disk.findtext('sn'),
                        "size":  int(disk.findtext('size')),
                        "failed":  bool(int(disk.findtext('failed'))),
                        "healthy":  bool(int(disk.findtext('healthy'))),
                        "removable":  bool(int(disk.findtext('removable'))),
                        "over_temp":  bool(int(disk.findtext('over_temp'))),
                        "temp": int(disk.findtext('temp')),
                        "sleep":  bool(int(disk.findtext('sleep')))
                    })
                for disk in device_info.iter('vol'):
                    device_info_json['volumes'].append({
                        "id": disk.attrib['id'],
                        "name":  disk.findtext('name'),
                        "label":  disk.findtext('label'),
                        "encrypted":  bool(int(disk.findtext('encrypted'))),
                        "unlocked":  bool(int(disk.findtext('unlocked'))),
                        "mounted":  bool(int(disk.findtext('mounted'))),
                        "size":  int(disk.findtext('size')),
                    })
                device_info_json['size']['total'] =int(device_info.find('.//total_size').text)
                device_info_json['size']['used'] = int(device_info.find('.//total_used_size').text)
                device_info_json['size']['unused'] = int(device_info.find('.//total_unused_size').text)
                return device_info_json
            else:
                raise RequestFailedError(response.status)
    
    async def share_names(self) -> list:
        """Fetches a list of all share names configured on the device."""
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['share_names']}"

        if self.version == 2:     
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {
                "Host": self.host
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                if json_content['success']:
                    return json_content['item']
                else:
                    raise RequestFailedError(response.status)
            else:
                raise RequestFailedError(response.status)
    
    async def system_status(self) -> dict:
        """
        Fetches system status from the device.

        This includes data about **CPU** and **memory** usage.
        """
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_status']}"

        if self.version == 2:     
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            data = 'cmd=resource'
        elif self.version == 5:
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            data = 'cmd=resource'
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_status = ElementTree.fromstring(content)
                json_device_status = {"memory": {}, "cpu": None}
                json_device_status['cpu'] = int(device_status.find('.//cpu').text.strip('%'))
                json_device_status['memory']['total'] = int(device_status.find('.//mem_total').text)
                json_device_status['memory']['unused'] = int(device_status.find('.//mem_free').text)
                json_device_status['memory']['simple'] = device_status.find('.//mem2_total').text

                for xml_name, output_name in (
                    ("buffers", "buffers"),
                    ("cached", "cached"),
                ):
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        json_device_status["memory"][output_name] = _optional_int(value)

                for xml_name, output_name in (
                    ("bonding", "bonding"),
                    ("now_time", "now_time"),
                ):
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        json_device_status[output_name] = _optional_int(value)

                network_fields = {
                    "lan_r_speed": ("lan", "received_raw"),
                    "lan_t_speed": ("lan", "transmitted_raw"),
                    "lan2_r_speed": ("lan2", "received_raw"),
                    "lan2_t_speed": ("lan2", "transmitted_raw"),
                }
                network = {}
                for xml_name, (interface, output_name) in network_fields.items():
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        network.setdefault(interface, {})[output_name] = _optional_int(value)
                if network:
                    json_device_status["network"] = network

                processes = device_status.findall(".//process_main")
                if processes:
                    json_device_status["process_count"] = len(processes)

                known_fields = {
                    "bonding", "buffers", "cached", "cpu", "lan_r_speed",
                    "lan_t_speed", "lan2_r_speed", "lan2_t_speed", "mem_total",
                    "mem2_total", "mem_free", "now_time", "process_main",
                }
                extra = _xml_children(device_status, known_fields)
                if extra:
                    json_device_status["extra"] = extra
                return json_device_status
            else:
                raise RequestFailedError(response.status)
    
    async def network_info(self) -> dict:
        """
        Fetches network information from the device.

        This includes device **IP**, **MAC Address** and **Speed**.
        """
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['network_info']}"

        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {
                "Host": self.host
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                network_info = ElementTree.fromstring(content)
                json_network_info = {}
                for lan in network_info.iter('lan'):
                    mac = lan.findtext('mac')
                    if mac != 'No found.':
                        json_network_info[mac] = {
                            "speed": lan.findtext('speed'),
                            "dhcp_enable": bool(int(lan.findtext('dhcp_enable'))),
                            "dns_manual": bool(int(lan.findtext('dns_manual'))),
                            "ip": lan.findtext('ip'),
                            "netmask": lan.findtext('netmask'),
                            "gateway": lan.findtext('gateway'),
                            "lan_speed": lan.findtext('lan_speed'),
                            "lan_enabled": bool(int(lan.findtext('lan_status'))),
                            "dns1": lan.findtext('dns1'),
                            "dns2": lan.findtext('dns2'),
                            "dns3": lan.findtext('dns3')
                        }
                return json_network_info
            else:
                raise RequestFailedError(response.status)

    async def device_info(self) -> dict:
        """
        Fetches device information from the device.

        Data such as **Serial Number**, **Name** and **Description** are returned.
        """
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['device_info']}"
        if self.version == 2:
            data = 'cmd=cgi_get_device_info'
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            data = 'cmd=cgi_get_device_info'
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_info = ElementTree.fromstring(content)

                json_device_info = {"serial_number": None, "name": None, "description": None}

                serial_number_node = device_info.find('.//serial_number')
                name_node = device_info.find('.//name')
                description_node = device_info.find('.//description')

                json_device_info["serial_number"] = serial_number_node.text
                json_device_info["name"] = name_node.text
                json_device_info["description"] = description_node.text

                return json_device_info
            else:
                raise RequestFailedError(response.status)

    async def system_version(self) -> dict:
        """Fetches system firmware version from the device."""
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_version']}"
        if self.version == 2:
            data = 'cmd=get_firm_v_xml'
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            data = 'cmd=get_firm_v_xml'
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_version = ElementTree.fromstring(content)
                json_device_version = {"firmware": None, "oled": None}

                firmware_node = device_version.find('.//fw')
                oled_node = device_version.find('.//oled')

                json_device_version["firmware"] = firmware_node.text

                if oled_node.text is not None:
                    json_device_version["oled"] = oled_node.text.strip('\n')

                return json_device_version
            else:
                raise RequestFailedError(response.status)
                     
    async def latest_version(self) -> dict:
        """
        Fetches the latest firmware version from the device.

        Only devices with **V2** OS are supported.
        """
        if self.version != 2:
            raise ValueError("Unsupported/invalid version. Must be 2.")
        
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['device_info']}"
        data = 'cmd=get_auto_fw_version'
        headers = {
            "Host": self.host,
            "X-CSRF-Token": self.wd_csrf_token,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                latest_version = ElementTree.fromstring(content)
                json_latest_version = {"new": None, "details": {}}

                new_node = latest_version.find('.//new')
                version_node = latest_version.find('.//version')
                path_node = latest_version.find('.//path')
                releasenote_node = latest_version.find('.//releasenote')


                if new_node.text is not None:
                    json_latest_version["new"] = bool(int(new_node.text))
                else:
                    json_latest_version["new"] = False
   
                json_latest_version["details"]["version"] = version_node.text
                json_latest_version["details"]["path"] = path_node.text
                json_latest_version["details"]["releasenote"] = releasenote_node.text
                
                return json_latest_version
            else:
                raise RequestFailedError(response.status)
    
    async def accounts(self) -> dict:
        """Fetches account information from the device."""
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['accounts']}"
        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {
                "Host": self.host,
            }
        else:
            raise ValueError("Unsupported/invalid version.")
        
        async with self.session.post(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                accounts = ElementTree.fromstring(content)
                json_accounts = {"users": [], "groups": {}}
                for user in accounts.iter('item'):
                    uid = user.findtext('uid')
                    if user.findtext('pwd') is not None:
                        password_bool = bool(int(user.findtext('pwd')))
                    else:
                        password_bool = False
                    last_name_list = []
                    for lastName in user.iter('last_name'):
                        last_name_list.append(lastName.text)
                    json_accounts['users'].append({
                        "uid": uid,
                        "name": user.findtext('name'),
                        "email": user.findtext('email'),
                        "pwd": password_bool,
                        "gid": user.findtext('gid'),
                        "first_name": user.findtext('first_name'),
                        "last_name": last_name_list,
                        "hint": user.findtext('hint'),
                    })
                for group in accounts.iter('item'):
                    gid = group.findtext('gid')
                    json_accounts['groups'][gid] = {
                        "name": group.findtext('name'),
                        "user_cnt": user.findtext('user_cnt'),
                    }
                    json_accounts['groups'][gid]['users'] = []
                    for user in group.iter('users'):
                        json_accounts['groups'][gid]['users'].append(user.findtext('user'))
                return json_accounts
            else:
                raise RequestFailedError(response.status)
    
    async def alerts(self) -> tuple:
        """Retrieves the current system alerts and notifications from the device."""
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['alerts']}"
        data = 'cmd=cgi_get_alert'
        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")
        
        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                alerts_xml = ElementTree.fromstring(content)
                alerts_list = []
                for entry in alerts_xml.iter("alerts"):
                    alerts_list.append({
                        "code": entry.findtext("code"),
                        "seq_num": entry.findtext("seq_num"),
                        "level": entry.findtext("level"),
                        "msg": entry.findtext("msg"),
                        "desc": entry.findtext("desc"),
                        "time": entry.findtext("time")
                    })
        
                return len(alerts_list), alerts_list
            else:
                raise RequestFailedError(response.status)

    async def diagnostics(self) -> dict:
        """Fetch disk, fan, board, and RAID diagnostic information."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['diagnostics']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(url, data="cmd=2", headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            known_root_fields = {"board_temperature", "fan", "hd_status", "raid"}
            hd_status = root.find("hd_status")
            raid = root.find("raid")
            result = {
                "board_temperature": _optional_int(root.findtext("board_temperature")),
                "fan_rpm": _optional_int(root.findtext("fan")),
                "disk_statuses": [disk.text for disk in root.findall("./hd_status/disk")],
                "disk_status_extra": (
                    _xml_children(hd_status, {"disk"})
                    if hd_status is not None
                    else {}
                ),
                "raid": {
                    "volume_count": _optional_int(raid.findtext("cntVol")) if raid is not None else None,
                    "result": _optional_int(raid.findtext("res")) if raid is not None else None,
                    "disk_count": _optional_int(raid.findtext("cntDisk")) if raid is not None else None,
                    "volumes": [],
                    "extra": {},
                },
                "extra": {},
            }

            if raid is not None:
                for item in raid.findall("item"):
                    known_item_fields = {"num", "vol", "raidmode", "state"}
                    result["raid"]["volumes"].append({
                        "id": item.get("id"),
                        "number": _optional_int(item.findtext("num")),
                        "volume": item.findtext("vol"),
                        "mode": item.findtext("raidmode"),
                        "state": item.findtext("state"),
                        "extra": _xml_children(item, known_item_fields),
                    })
                result["raid"]["extra"] = _xml_children(
                    raid, {"cntVol", "res", "cntDisk", "item"}
                )

            result["extra"] = _xml_children(root, known_root_fields)
            return result

    async def capacity_usage(self) -> list:
        """Return the API's raw per-volume capacity and media-category values."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['capacity_usage']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(
            url, data="cmd=cgi_Status_CapacityUsage", headers=headers
        ) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            field_names = {
                "volume": "volume",
                "total_size": "total_size",
                "use_size": "used_size",
                "free_size": "free_size",
                "used_rate": "used_rate",
                "Shared_Pictures": "shared_pictures",
                "Shared_Music": "shared_music",
                "Shared_Videos": "shared_videos",
            }
            volumes = []
            for item in root.findall(".//item"):
                volume = {
                    output_name: _optional_int(item.findtext(xml_name))
                    for xml_name, output_name in field_names.items()
                }
                volume["extra"] = _xml_children(item, field_names)
                volumes.append(volume)
            return volumes

    async def volume_status(self) -> list:
        """Return detailed read-only volume, filesystem, and RAID status."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['volume_status']}"
        headers = {"Host": self.host}
        async with self.session.get(url, headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            numeric_fields = {
                "volume", "web_cmd", "sdx3_flag", "raid_status", "size",
                "used_size", "free_size", "percentage", "dev_num",
                "volume_encrypt", "scsi_mapping", "is_roaming_volume",
            }
            known_fields = {
                "volume", "raid_mode", "raid_status", "file_type", "size",
                "used_size", "free_size", "percentage", "device", "dev_num",
                "mount", "mount_status", "volume_encrypt", "is_roaming_volume",
                "scsi_mapping", "spare", "to_be_sync", "web_cmd", "sdx3_flag",
                "used_device", "raid_uuid", "scsi0_serial", "scsi1_serial",
            }
            volume_info = root if root.tag == "volume_info" else root.find(".//volume_info")
            volumes = []
            items = volume_info.findall("item") if volume_info is not None else []
            for item in items:
                volume = {}
                for field in known_fields:
                    value = item.findtext(field)
                    volume[field] = (
                        _optional_int(value) if field in numeric_fields else value
                    )
                volume["extra"] = _xml_children(item, known_fields)
                volumes.append(volume)
            return volumes

    async def system_warning(self) -> dict:
        """Return the OS 5 temperature/busy warning code and known meaning."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_warning']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(url, data="cmd=8", headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            code = _optional_int(root.findtext(".//res"))
            statuses = {
                0: "normal",
                1: "over_temperature",
                2: "system_busy_hotplug",
            }
            return {"code": code, "status": statuses.get(code, "unknown")}

    async def internet_access(self) -> bool:
        """Return only the internet-connectivity assessment reported by the NAS."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['internet_access']}"
        async with self.session.get(url, headers={"Host": self.host}) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            value = root.findtext(".//connectivity")
            if value is None:
                raise ValueError("NAS response has no connectivity value")
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
            raise ValueError(f"Unknown NAS connectivity value: {value!r}")

    async def process_info(self) -> list:
        """Return the diagnostic process list reported by OS 5."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['process_info']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(
            url, data="cmd=cgi_process", headers=headers
        ) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            processes = []
            for item in root.findall(".//process_main"):
                known_fields = {"command", "user", "pid", "cpu", "mem"}
                processes.append({
                    "command": item.findtext("command"),
                    "user": item.findtext("user"),
                    "pid": _optional_int(item.findtext("pid")),
                    "cpu": _optional_number(item.findtext("cpu")),
                    "mem": _optional_number(item.findtext("mem")),
                    "extra": _xml_children(item, known_fields),
                })
            return processes
    
    async def cloud_access(self) -> dict:
        """
        Fetches cloud access information from the device.

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")
        
        url = f"{SCHEME}{self.host}/web/restSDK/cloudAccess.php"

        data = 'cmd=getCloudAccess'
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                return json_content
            else:
                raise RequestFailedError(response.status)

    async def usb_info(self) -> dict:
        """
        Fetches connected USB devices from the device.

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")
        
        url = f"{SCHEME}{self.host}/web/get_usb_info.php"

        headers = {
            "Host": self.host
        }

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                return json_content
            else:
                raise RequestFailedError(response.status)
    
    async def uptime(self) -> int:
        """
        Returns the device uptime in seconds (parsed).

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")
    
        url = f"{SCHEME}{self.host}/cgi-bin/status_mgr.cgi"
    
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
    
        data = "cmd=cgi_get_uptime"
    
        async with self.session.post(url, data=data, headers=headers) as response:
            text = await response.text()

            if response.status != 200:
                if response.status == 500 and not text.strip():
                    raise UptimeUnavailableError(response.status)
                raise RequestFailedError(response.status)
    
            match = re.search(r"<uptime>(.*?)</uptime>", text, re.S)
            if not match:
                raise UptimeUnavailableError()
    
            uptime_raw = match.group(1).strip()
    
            match = re.search(
                r"(\d+)\s*days?\s*(\d+)\s*hour[s]?\s*(\d+)\s*minute[s]?",
                uptime_raw,
                re.I,
            )
    
            if not match:
                raise UptimeUnavailableError()
    
            d, h, m = map(int, match.groups())
            return int(timedelta(days=d, hours=h, minutes=m).total_seconds())
