import asyncio
import sys

from backend.scheduler import _run_monitor
from backend.database import get_active_monitors

async def main():
    monitors = get_active_monitors()
    if not monitors:
        print("No active monitors found!")
        return
    
    # Run the first one (Anestezi)
    mon = monitors[0]
    print(f"Testing monitor: {mon['search_text']}")
    
    loop = asyncio.get_running_loop()
    await _run_monitor(mon, loop)

if __name__ == "__main__":
    asyncio.run(main())
