import asyncio
import http.cookies
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import aiohttp

from wdnas_client import client
from wdnas_client.exceptions import (
    InvalidLoginError,
    PowerActionBlockedError,
    RequestFailedError,
    UptimeUnavailableError,
)


class Headers(dict):
    def getall(self, key, default=None):
        value = self.get(key)
        if value is None:
            return [] if default is None else default
        return value if isinstance(value, list) else [value]


class FakeResponse:
    def __init__(self, status=200, text="", cookies=None, headers=None):
        self.status = status
        self._text = text
        self.cookies = cookies or http.cookies.SimpleCookie()
        self.headers = Headers(headers or {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._text


class CookieJar:
    def __init__(self):
        self.updates = []

    def update_cookies(self, cookies):
        self.updates.append(cookies)


class FakeSession:
    def __init__(self, response):
        self.responses = list(response) if isinstance(response, (list, tuple)) else None
        self.response = response
        self.calls = []
        self.cookie_jar = CookieJar()
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.responses.pop(0) if self.responses is not None else self.response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def delete(self, url, **kwargs):
        self.calls.append(("DELETE", url, kwargs))
        return self.responses.pop(0) if self.responses is not None else self.response

    async def close(self):
        self.closed = True


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, response, version=5):
        nas = client("Admin", "secret", "nas.local", version)
        nas.session = FakeSession(response)
        return nas

    def make_power_client(self, response, version=5):
        nas = self.make_client(response, version)
        nas.phpsessid = "session-id"
        return nas

    async def test_os5_login(self):
        cookies = http.cookies.SimpleCookie()
        cookies["PHPSESSID"] = "session-id"
        response = FakeResponse(
            cookies=cookies,
            headers={"Set-Cookie": "PHPSESSID=session-id; Path=/"},
        )
        nas = self.make_client(response)
        await nas.login()
        self.assertEqual(nas.phpsessid, "session-id")
        method, url, kwargs = nas.session.calls[0]
        self.assertEqual((method, url), ("POST", "http://nas.local/nas/v1/auth"))
        self.assertEqual(kwargs["json"]["username"], "admin")
        self.assertEqual(kwargs["json"]["password"], "c2VjcmV0")

    async def test_os5_login_requires_session_cookie(self):
        nas = self.make_client(FakeResponse())
        with self.assertRaises(InvalidLoginError):
            await nas.login()

    async def test_os2_login_remains_compatible(self):
        cookies = http.cookies.SimpleCookie()
        cookies["PHPSESSID"] = "session-id"
        cookies["WD-CSRF-TOKEN"] = "csrf-token"
        response = FakeResponse(cookies=cookies)
        nas = self.make_client(response, version=2)
        await nas.login()
        self.assertEqual(nas.phpsessid, "session-id")
        self.assertEqual(nas.wd_csrf_token, "csrf-token")
        method, url, kwargs = nas.session.calls[0]
        self.assertEqual(
            (method, url), ("POST", "http://nas.local/cgi-bin/login_mgr.cgi")
        )
        self.assertIn("cmd=wd_login", kwargs["data"])

    async def test_os5_logout_uses_proven_endpoint_and_existing_session(self):
        nas = self.make_power_client(FakeResponse(status=204))

        await nas.logout()

        self.assertEqual(
            nas.session.calls,
            [
                (
                    "DELETE",
                    "http://nas.local/nas/v1/auth",
                    {"headers": {"Accept": "application/json", "Host": "nas.local"}},
                )
            ],
        )
        self.assertIsNone(nas.phpsessid)
        self.assertFalse(nas.session.closed)

    async def test_close_logs_out_then_closes_local_session(self):
        nas = self.make_power_client(FakeResponse())

        await nas.close()

        self.assertEqual(nas.session.calls[0][0], "DELETE")
        self.assertTrue(nas.session.closed)
        self.assertIsNone(nas.phpsessid)

    async def test_close_is_idempotent_and_concurrent_safe(self):
        nas = self.make_power_client(FakeResponse())

        await asyncio.gather(nas.close(), nas.close(), nas.close())
        await nas.close()

        self.assertEqual(
            [call[0] for call in nas.session.calls].count("DELETE"),
            1,
        )

    async def test_close_reports_logout_http_error_after_local_cleanup(self):
        nas = self.make_power_client(FakeResponse(status=503))

        with self.assertRaises(RequestFailedError) as caught:
            await nas.close()

        self.assertEqual(caught.exception.status_code, 503)
        self.assertTrue(nas.session.closed)
        with self.assertRaises(RequestFailedError):
            await nas.close()
        self.assertEqual(len(nas.session.calls), 1)

    async def test_context_exit_does_not_mask_original_exception(self):
        nas = self.make_power_client(FakeResponse(status=503))

        original = RuntimeError("original failure")
        with self.assertRaises(RuntimeError) as caught:
            try:
                raise original
            except RuntimeError:
                await nas.__aexit__(RuntimeError, original, original.__traceback__)
                raise

        self.assertIs(caught.exception, original)
        self.assertTrue(nas.session.closed)

    async def test_os2_close_remains_local_and_does_not_call_logout_endpoint(self):
        nas = self.make_client(FakeResponse(), version=2)
        nas.phpsessid = "session-id"
        nas.wd_csrf_token = "csrf-token"

        await nas.close()

        self.assertEqual(nas.session.calls, [])
        self.assertTrue(nas.session.closed)

        with self.assertRaises(ValueError):
            await nas.logout()

    async def test_failed_context_login_closes_local_session(self):
        nas = client("Admin", "secret", "nas.local", 5)
        session = FakeSession(FakeResponse(status=401))

        with (
            patch("wdnas_client.aiohttp.ClientSession", return_value=session),
            self.assertRaises(RequestFailedError),
        ):
            await nas.__aenter__()

        self.assertTrue(session.closed)
        self.assertIsNone(nas.phpsessid)

    async def test_failed_login_cleanup_does_not_mask_login_error(self):
        class FailingCloseSession(FakeSession):
            async def close(self):
                self.closed = True
                raise RuntimeError("local cleanup failed")

        nas = client("Admin", "secret", "nas.local", 5)
        session = FailingCloseSession(FakeResponse(status=401))

        with (
            patch("wdnas_client.aiohttp.ClientSession", return_value=session),
            self.assertRaises(RequestFailedError) as caught,
        ):
            await nas.__aenter__()

        self.assertEqual(caught.exception.status_code, 401)
        self.assertTrue(session.closed)

    async def test_logout_timeout_closes_locally_and_is_not_reported_as_success(self):
        class SlowDeleteResponse:
            async def __aenter__(self):
                await asyncio.Event().wait()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        nas = self.make_power_client(SlowDeleteResponse())

        with (
            patch("wdnas_client.SESSION_ACTION_TIMEOUT", 0.01),
            self.assertRaises(TimeoutError),
        ):
            await nas.close()

        self.assertTrue(nas.session.closed)

    async def test_logout_disconnect_and_invalid_session_are_errors(self):
        class DisconnectResponse:
            async def __aenter__(self):
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        disconnected = self.make_power_client(DisconnectResponse())
        with self.assertRaises(aiohttp.ServerDisconnectedError):
            await disconnected.close()
        self.assertTrue(disconnected.session.closed)

        invalid = self.make_power_client(FakeResponse(status=403))
        with self.assertRaises(RequestFailedError) as caught:
            await invalid.close()
        self.assertEqual(caught.exception.status_code, 403)
        self.assertTrue(invalid.session.closed)

    async def test_logout_without_authenticated_session_does_not_log_in(self):
        nas = self.make_client(FakeResponse())

        await nas.logout()

        self.assertEqual(nas.session.calls, [])

    async def test_diagnostics_healthy_raid1(self):
        xml = """<config>
          <board_temperature>0</board_temperature><fan>0</fan>
          <hd_status><disk>healthy</disk><disk>healthy</disk></hd_status>
          <raid><cntVol>1</cntVol><res>1</res><cntDisk>2</cntDisk>
            <item id="1"><num>1</num><vol>Volume_1</vol>
            <raidmode>raid1</raidmode><state>clean</state></item>
          </raid></config>"""
        nas = self.make_client(FakeResponse(text=xml))
        result = await nas.diagnostics()
        self.assertEqual(result["board_temperature"], 0)
        self.assertEqual(result["fan_rpm"], 0)
        self.assertEqual(result["disk_statuses"], ["healthy", "healthy"])
        self.assertEqual(result["raid"]["volumes"][0]["mode"], "raid1")
        self.assertEqual(result["raid"]["volumes"][0]["state"], "clean")
        self.assertEqual(nas.session.calls[0][2]["data"], "cmd=2")

    async def test_diagnostics_raid_states_and_multiple_volumes(self):
        for state in (
            "degraded",
            "damaged",
            "resync",
            "resync5",
            "recovery",
            "migrate",
            "reshape",
            "expansion",
        ):
            with self.subTest(state=state):
                xml = f"""<config><raid><cntVol>2</cntVol><cntDisk>2</cntDisk>
                  <item id="1"><num>1</num><vol>A</vol><raidmode>raid1</raidmode><state>{state}</state></item>
                  <item id="2"><num>2</num><vol>B</vol><raidmode>jbod</raidmode><state>clean</state></item>
                </raid></config>"""
                result = await self.make_client(FakeResponse(text=xml)).diagnostics()
                self.assertEqual(result["raid"]["volume_count"], 2)
                self.assertEqual(len(result["raid"]["volumes"]), 2)
                self.assertEqual(result["raid"]["volumes"][0]["state"], state)

    async def test_diagnostics_missing_and_unknown_fields(self):
        xml = """<config><mystery><value>7</value></mystery>
          <hd_status><disk>unknown</disk><detail source="wd">kept</detail></hd_status><raid>
          <item id="9"><state>reshape</state><progress>42</progress></item>
          <vendor_flag>enabled</vendor_flag></raid></config>"""
        result = await self.make_client(FakeResponse(text=xml)).diagnostics()
        self.assertIsNone(result["board_temperature"])
        self.assertEqual(result["disk_statuses"], ["unknown"])
        self.assertEqual(
            result["disk_status_extra"]["detail"],
            {"value": "kept", "attributes": {"source": "wd"}},
        )
        self.assertEqual(result["extra"]["mystery"], {"value": 7})
        self.assertEqual(result["raid"]["extra"]["vendor_flag"], "enabled")
        self.assertEqual(result["raid"]["volumes"][0]["extra"]["progress"], 42)

    async def test_capacity_usage_one_and_multiple_volumes(self):
        xml = """<config>
          <item><volume>1</volume><total_size>1918590988</total_size>
            <use_size>1918589500</use_size><free_size>1488</free_size><used_rate>100</used_rate>
            <Shared_Pictures>10</Shared_Pictures><Shared_Music>20</Shared_Music>
            <Shared_Videos>30</Shared_Videos><other>kept</other></item>
          <item><volume>2</volume><total_size>1000</total_size>
            <use_size>250</use_size><free_size>750</free_size><used_rate>25</used_rate></item>
        </config>"""
        nas = self.make_client(FakeResponse(text=xml))
        result = await nas.capacity_usage()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["total_size"], 1918590988)
        self.assertEqual(result[0]["used_size"], 1918589500)
        self.assertEqual(result[0]["free_size"], 1488)
        self.assertEqual(result[0]["shared_pictures"], 10)
        self.assertEqual(result[0]["extra"], {"other": "kept"})
        self.assertEqual(result[1]["used_rate"], 25)
        self.assertIsNone(result[1]["shared_videos"])

    async def test_usb_success_false_is_returned_and_get_is_used(self):
        nas = self.make_client(FakeResponse(text='{"success": false}'))
        result = await nas.usb_info()
        self.assertEqual(result, {"success": False})
        self.assertEqual(nas.session.calls[0][0], "GET")

    async def test_uptime_http_500_raises_compatible_exception(self):
        nas = self.make_client(FakeResponse(status=500))
        with self.assertRaises(UptimeUnavailableError) as caught:
            await nas.uptime()
        self.assertIsInstance(caught.exception, RequestFailedError)
        self.assertEqual(caught.exception.status_code, 500)

    async def test_uptime_other_server_error_remains_request_failed(self):
        nas = self.make_client(FakeResponse(status=503, text="Service unavailable"))
        with self.assertRaises(RequestFailedError) as caught:
            await nas.uptime()
        self.assertNotIsInstance(caught.exception, UptimeUnavailableError)

    async def test_existing_system_status_parser(self):
        xml = """<config><cpu>12%</cpu><mem_total>1024</mem_total>
          <mem_free>256</mem_free><mem2_total>1 GB</mem2_total></config>"""
        result = await self.make_client(FakeResponse(text=xml)).system_status()
        self.assertEqual(
            result,
            {
                "memory": {"total": 1024, "unused": 256, "simple": "1 GB"},
                "cpu": 12,
            },
        )

    async def test_existing_uptime_parser(self):
        xml = "<config><uptime>2 days 3 hours 4 minutes</uptime></config>"
        result = await self.make_client(FakeResponse(text=xml)).uptime()
        self.assertEqual(result, 183840)

    async def test_volume_status_raid1_ext4_and_sensitive_metadata(self):
        xml = """<config><volume_info><item>
          <volume>1</volume><raid_mode>raid1</raid_mode><raid_status>0</raid_status>
          <file_type>ext4</file_type><size>1918590988</size><used_size>100</used_size>
          <free_size>1918590888</free_size><percentage>1</percentage>
          <device>sdasdb</device><dev_num>2</dev_num><mount>/mnt/HD/HD_a2</mount>
          <mount_status>mounted</mount_status><volume_encrypt>0</volume_encrypt>
          <raid_uuid>sensitive-uuid</raid_uuid><scsi0_serial>sensitive-serial</scsi0_serial>
          <vendor_field>kept</vendor_field></item></volume_info></config>"""
        nas = self.make_client(FakeResponse(text=xml))
        result = await nas.volume_status()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["raid_mode"], "raid1")
        self.assertEqual(result[0]["raid_status"], 0)
        self.assertEqual(result[0]["file_type"], "ext4")
        self.assertEqual(result[0]["dev_num"], 2)
        self.assertEqual(result[0]["mount_status"], "mounted")
        self.assertEqual(result[0]["volume_encrypt"], 0)
        self.assertEqual(result[0]["raid_uuid"], "sensitive-uuid")
        self.assertEqual(result[0]["scsi0_serial"], "sensitive-serial")
        self.assertEqual(result[0]["extra"], {"vendor_field": "kept"})
        self.assertEqual(nas.session.calls[0][0], "GET")

    async def test_volume_status_multiple_missing_and_unknown_raid_status(self):
        xml = """<config><volume_info>
          <item><volume>1</volume><raid_status>vendor-state</raid_status></item>
          <item><volume>2</volume><raid_mode>jbod</raid_mode></item>
        </volume_info></config>"""
        result = await self.make_client(FakeResponse(text=xml)).volume_status()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["raid_status"], "vendor-state")
        self.assertIsNone(result[0]["file_type"])
        self.assertEqual(result[1]["volume"], 2)

    async def test_volume_status_accepts_volume_info_as_document_root(self):
        xml = "<volume_info><item><volume>7</volume></item></volume_info>"
        result = await self.make_client(FakeResponse(text=xml)).volume_status()
        self.assertEqual(result[0]["volume"], 7)

    async def test_system_warning_known_and_unknown_codes(self):
        expected = {
            0: "normal",
            1: "over_temperature",
            2: "system_busy_hotplug",
            99: "unknown",
        }
        for code, status in expected.items():
            with self.subTest(code=code):
                nas = self.make_client(
                    FakeResponse(text=f"<config><res>{code}</res></config>")
                )
                self.assertEqual(
                    await nas.system_warning(), {"code": code, "status": status}
                )

    async def test_internet_access_true_and_false(self):
        for value, expected in (("true", True), ("FALSE", False)):
            with self.subTest(value=value):
                xml = f"<internet_access><connectivity>{value}</connectivity></internet_access>"
                result = await self.make_client(
                    FakeResponse(text=xml)
                ).internet_access()
                self.assertIs(result, expected)

    async def test_internet_access_missing_or_unknown_is_not_false(self):
        for xml in (
            "<internet_access/>",
            "<internet_access><connectivity>unknown</connectivity></internet_access>",
        ):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                await self.make_client(FakeResponse(text=xml)).internet_access()

    async def test_process_info_empty_and_multiple(self):
        empty = await self.make_client(FakeResponse(text="<xml/>")).process_info()
        self.assertEqual(empty, [])

        xml = """<xml>
          <process_main><command>alpha</command><user>root</user><pid>12</pid>
            <cpu>28.1</cpu><mem>1524</mem></process_main>
          <process_main><command>beta</command><pid>13</pid><cpu>0</cpu></process_main>
        </xml>"""
        nas = self.make_client(FakeResponse(text=xml))
        result = await nas.process_info()
        self.assertEqual(
            result[0],
            {
                "command": "alpha",
                "user": "root",
                "pid": 12,
                "cpu": 28.1,
                "mem": 1524,
                "extra": {},
            },
        )
        self.assertIsNone(result[1]["user"])
        self.assertIsNone(result[1]["mem"])
        self.assertEqual(nas.session.calls[0][2]["data"], "cmd=cgi_process")

    async def test_process_info_preserves_bad_numeric_values(self):
        xml = """<xml><process_main><pid>not-a-pid</pid><cpu>busy</cpu>
          <mem>unknown</mem><vendor>kept</vendor></process_main></xml>"""
        result = await self.make_client(FakeResponse(text=xml)).process_info()
        self.assertEqual(result[0]["pid"], "not-a-pid")
        self.assertEqual(result[0]["cpu"], "busy")
        self.assertEqual(result[0]["mem"], "unknown")
        self.assertEqual(result[0]["extra"], {"vendor": "kept"})

    async def test_extended_system_status_preserves_legacy_fields(self):
        xml = """<xml><bonding>0</bonding><buffers>31360</buffers>
          <cached>220544</cached><cpu>4%</cpu><lan_r_speed>13191338173</lan_r_speed>
          <lan_t_speed>11278793837</lan_t_speed><lan2_r_speed>3</lan2_r_speed>
          <lan2_t_speed>4</lan2_t_speed><mem_total>509780</mem_total>
          <mem2_total>512MB</mem2_total><mem_free>20500</mem_free>
          <now_time>1792870243</now_time><process_main/><process_main/>
          <vendor_resource>kept</vendor_resource></xml>"""
        result = await self.make_client(FakeResponse(text=xml)).system_status()
        self.assertEqual(result["cpu"], 4)
        self.assertEqual(result["memory"]["total"], 509780)
        self.assertEqual(result["memory"]["unused"], 20500)
        self.assertEqual(result["memory"]["simple"], "512MB")
        self.assertEqual(result["memory"]["buffers"], 31360)
        self.assertEqual(result["memory"]["cached"], 220544)
        self.assertEqual(result["network"]["lan"]["received_raw"], 13191338173)
        self.assertEqual(result["network"]["lan2"]["transmitted_raw"], 4)
        self.assertEqual(result["bonding"], 0)
        self.assertEqual(result["now_time"], 1792870243)
        self.assertEqual(result["process_count"], 2)
        self.assertEqual(result["extra"], {"vendor_resource": "kept"})

    async def test_new_methods_http_errors(self):
        for method_name in (
            "volume_status",
            "system_warning",
            "internet_access",
            "process_info",
        ):
            with self.subTest(method=method_name):
                nas = self.make_client(FakeResponse(status=503))
                with self.assertRaises(RequestFailedError):
                    await getattr(nas, method_name)()

    async def test_new_methods_invalid_xml(self):
        for method_name in (
            "volume_status",
            "system_warning",
            "internet_access",
            "process_info",
        ):
            with self.subTest(method=method_name):
                nas = self.make_client(FakeResponse(text="<invalid"))
                with self.assertRaises(ElementTree.ParseError):
                    await getattr(nas, method_name)()

    async def test_new_methods_are_os5_only(self):
        for method_name in (
            "volume_status",
            "system_warning",
            "internet_access",
            "process_info",
        ):
            with self.subTest(method=method_name):
                nas = self.make_client(FakeResponse(), version=2)
                with self.assertRaises(ValueError):
                    await getattr(nas, method_name)()
                self.assertEqual(nas.session.calls, [])

    async def test_reboot_runs_safety_check_then_action(self):
        nas = self.make_power_client(
            [FakeResponse(text="<config><res>0</res></config>"), FakeResponse()]
        )
        await nas.reboot()
        self.assertEqual(
            [call[2]["data"] for call in nas.session.calls],
            ["cmd=cgi_Detect_Dangerous", "cmd=cgi_restart"],
        )

    async def test_shutdown_runs_safety_check_then_action(self):
        nas = self.make_power_client(
            [FakeResponse(text="<config><res>0</res></config>"), FakeResponse()]
        )
        await nas.shutdown()
        self.assertEqual(nas.session.calls[1][2]["data"], "cmd=cgi_shutdown")

    async def test_power_action_is_blocked_before_action_request(self):
        nas = self.make_power_client(FakeResponse(text="<config><res>2</res></config>"))
        with self.assertRaises(PowerActionBlockedError):
            await nas.reboot()
        self.assertEqual(len(nas.session.calls), 1)

    async def test_power_action_accepts_only_server_disconnect(self):
        class DisconnectResponse:
            async def __aenter__(self):
                raise aiohttp.ServerDisconnectedError()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        nas = self.make_power_client(
            [
                FakeResponse(text="<config><res>0</res></config>"),
                DisconnectResponse(),
            ]
        )
        await nas.reboot()

        nas = self.make_power_client(
            [
                FakeResponse(text="<config><res>0</res></config>"),
                FakeResponse(status=503),
            ]
        )
        with self.assertRaises(RequestFailedError):
            await nas.reboot()

    async def test_power_actions_are_os5_only(self):
        for method_name in ("reboot", "shutdown"):
            with self.subTest(method=method_name):
                nas = self.make_client(FakeResponse(), version=2)
                with self.assertRaises(ValueError):
                    await getattr(nas, method_name)()
                self.assertEqual(nas.session.calls, [])

    async def test_power_action_rejects_unexpected_safety_responses(self):
        for xml in (
            "<config/>",
            "<config><res>busy</res></config>",
            "<invalid",
        ):
            with self.subTest(xml=xml):
                nas = self.make_power_client(FakeResponse(text=xml))
                with self.assertRaises((TypeError, ElementTree.ParseError)):
                    await nas.reboot()
                self.assertEqual(len(nas.session.calls), 1)

    async def test_power_action_timeout_never_sends_action(self):
        class SlowSafetyResponse:
            async def __aenter__(self):
                await asyncio.Event().wait()

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        nas = self.make_power_client(SlowSafetyResponse())
        with (
            patch("wdnas_client.POWER_ACTION_TIMEOUT", 0.01),
            self.assertRaises(TimeoutError),
        ):
            await nas.reboot()
        self.assertEqual(len(nas.session.calls), 1)

    async def test_power_action_requires_authentication(self):
        nas = self.make_client(FakeResponse(text="<config><res>0</res></config>"))
        with self.assertRaises(InvalidLoginError):
            await nas.reboot()
        self.assertEqual(nas.session.calls, [])

    async def test_power_safety_http_error_never_sends_action(self):
        nas = self.make_power_client(FakeResponse(status=401))
        with self.assertRaises(RequestFailedError) as caught:
            await nas.shutdown()
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(len(nas.session.calls), 1)


if __name__ == "__main__":
    unittest.main()
