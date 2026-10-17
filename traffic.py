import time
import urllib.error
import urllib.request
from collections import Counter

URL = "http://localhost:8002/orders"
INTERVAL = 0.2  # seconds between requests (about 5 per second)

counts = Counter()
last_print = time.time()

print(f"Sending traffic to {URL}. Press Ctrl+C to stop.")

try:
    while True:
        try:
            with urllib.request.urlopen(URL, timeout=10) as response:
                counts[response.status] += 1
        except urllib.error.HTTPError as error:
            counts[error.code] += 1
        except Exception:
            counts["connection_error"] += 1

        if time.time() - last_print >= 5:
            print(dict(counts))
            counts.clear()
            last_print = time.time()

        time.sleep(INTERVAL)
except KeyboardInterrupt:
    print("Stopped.")