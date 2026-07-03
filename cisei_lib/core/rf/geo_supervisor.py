import multiprocessing as mp
import queue

from cisei_lib.core.rf.rf_engine import RFEngine
import multiprocessing as mp

mp.set_start_method("spawn", force=True)

# ------------------------------------------------------------
# Worker loop (top-level, pickleable)
# ------------------------------------------------------------
def _rf_worker_loop(worker_id, max_links, task_q, result_q, rf_kwargs):
    """
    One worker = one RFEngine instance (owns geoInfo).
    """
    engine = RFEngine(**rf_kwargs)
    remaining = max_links

    while remaining > 0:
        try:
            item = task_q.get()
        except Exception:
            break

        if item is None:
            break  # supervisor shutdown

        req_id, payload = item

        try:
            result = engine.evaluate_link(payload)
            result_q.put((req_id, result, worker_id))
        except Exception as e:
            result_q.put((req_id, {"error": str(e)}, worker_id))

        remaining -= 1

    # exit silently by design
    return


# ------------------------------------------------------------
# Supervisor
# ------------------------------------------------------------
class GeoSupervisor:
    def __init__(self, num_workers: int, max_links_per_worker: int, **rf_kwargs):
        """
        rf_kwargs are passed verbatim to RFEngine (freq, diffraction method, etc.)
        """
        self.num_workers = num_workers
        self.max_links = max_links_per_worker
        self.rf_kwargs = rf_kwargs

        self.task_q = mp.Queue()
        self.result_q = mp.Queue()

        self.workers = {}
        self._worker_seq = 0

        for _ in range(num_workers):
            self._spawn_worker()

    # --------------------------------------------------------

    def _spawn_worker(self):
        wid = self._worker_seq
        self._worker_seq += 1

        p = mp.Process(
            target=_rf_worker_loop,
            args=(wid, self.max_links, self.task_q, self.result_q, self.rf_kwargs),
            daemon=True,
        )
        p.start()
        self.workers[wid] = p

    # --------------------------------------------------------

    def _reap_and_respawn(self):
        dead = [wid for wid, p in self.workers.items() if not p.is_alive()]
        for wid in dead:
            self.workers.pop(wid, None)
            self._spawn_worker()

    # --------------------------------------------------------

    def submit(self, req_id, payload):
        """
        payload = single link JSON (already split upstream)
        """
        self._reap_and_respawn()
        self.task_q.put((req_id, payload))

    # --------------------------------------------------------

    def collect(self, timeout=None):
        self._reap_and_respawn() # check if a worker have suicided
        try:
            return self.result_q.get(timeout=timeout)
        except queue.Empty:
            return None

    # --------------------------------------------------------

    def shutdown(self):
        for _ in self.workers:
            self.task_q.put(None)

        for p in self.workers.values():
            if p.is_alive():
                p.join(timeout=1)
                if p.is_alive():
                    p.terminate()

        self.workers.clear()
