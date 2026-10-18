"""Database access for the payment service."""
import yaml

CONFIG = yaml.safe_load(open("config.yaml"))
POOL_MAX = CONFIG["db"]["pool"]["max"]


class Pool:
    def __init__(self, size=POOL_MAX):
        self.size = size
        self.active = 0

    def acquire(self):
        if self.active >= self.size:
            raise TimeoutError("Connection pool exhausted: timeout waiting for connection")
        self.active += 1

    def release(self):
        self.active -= 1


def charge(pool, order_id):
    pool.acquire()
    result = run_query("INSERT INTO charges ...", order_id)  # BUG: if this raises, release() is never called
    pool.release()
    return result


def run_query(sql, *args):
    return {"ok": True}
