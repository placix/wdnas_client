import asyncio
from wdnas_client import client
from wdnas_client.exceptions import UptimeUnavailableError

async def main():
    username = input("Username: ").lower()
    password = input("Password: ")
    host = input("NAS IP address: ").strip()
    version = int(input("API version (2 or 5): ").strip())
    
    async with client(username, password, host, version) as wdNAS:
        # V2 and V5 systems
        print("System Info:", await wdNAS.system_info())
        print("Share Names:", await wdNAS.share_names())
        print("System Status:", await wdNAS.system_status())
        print("Network Info:", await wdNAS.network_info())
        print("Device Info:", await wdNAS.device_info())
        print("System Version:", await wdNAS.system_version())
        print("Accounts:", await wdNAS.accounts())
        print("Alerts:", await wdNAS.alerts())

        # V2 systems only
        if version == 2:
            print("Latest Version:", await wdNAS.latest_version())

        # V5 systems only
        if version == 5:
            print("Diagnostics:", await wdNAS.diagnostics())
            print("Capacity Usage:", await wdNAS.capacity_usage())
            print("USB Info:", await wdNAS.usb_info())
            try:
                print("Uptime:", await wdNAS.uptime())
            except UptimeUnavailableError as error:
                print("Uptime unavailable:", error)

            print("Cloud Access:", await wdNAS.cloud_access())

if __name__ == "__main__":
    asyncio.run(main())
