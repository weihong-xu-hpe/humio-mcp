import asyncio
import httpx

async def test():
    url = "https://mira-us-west-2.cloudops.ccs.arubathena.com/logs/api/v1/status"
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(url)
            print(f"verify=True: {resp.status_code}")
    except Exception as e:
        print(f"verify=True failed: {e}")

    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(url)
            print(f"verify=False: {resp.status_code}")
    except Exception as e:
        print(f"verify=False failed: {e}")

asyncio.run(test())
