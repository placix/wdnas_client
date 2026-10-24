import http.cookies
import unittest

from wdnas_client import client
from wdnas_client.exceptions import RequestFailedError, UptimeUnavailableError


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
        self.response = response
        self.calls = []
        self.cookie_jar = CookieJar()

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, response, version=5):
        nas = client("Admin", "secret", "nas.local", version)
        nas.session = FakeSession(response)
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
        for state in ("degraded", "damaged", "resync", "recovery", "migrate", "reshape", "expansion"):
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
        self.assertEqual(result, {
            "memory": {"total": 1024, "unused": 256, "simple": "1 GB"},
            "cpu": 12,
        })

    async def test_existing_uptime_parser(self):
        xml = "<config><uptime>2 days 3 hours 4 minutes</uptime></config>"
        result = await self.make_client(FakeResponse(text=xml)).uptime()
        self.assertEqual(result, 183840)


if __name__ == "__main__":
    unittest.main()
