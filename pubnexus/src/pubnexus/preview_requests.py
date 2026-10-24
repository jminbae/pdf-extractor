"""Bound concurrent preview work and cooperatively retire old selections."""
from collections import OrderedDict
from contextlib import contextmanager
import re
import threading


class PreviewCancelled(RuntimeError):
    pass


class PreviewRequests:
    def __init__(self, *, slots=2, clients=16):
        self._slots = threading.BoundedSemaphore(slots)
        self._clients = clients
        self._lock = threading.Lock()
        self._current = OrderedDict()

    def _event(self, client, generation, path):
        if client is None and generation is None:
            return threading.Event()
        if (not isinstance(client, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', client)
                or type(generation) is not int or not 0 <= generation <= 2**53 - 1):
            raise PreviewCancelled('미리보기 요청이 올바르지 않습니다.')
        with self._lock:
            previous = self._current.get(client)
            if previous:
                old_generation, old_path, old_event = previous
                if generation < old_generation:
                    raise PreviewCancelled('다른 문서로 이동했습니다.')
                if generation == old_generation:
                    if path is not None and old_path != path:
                        raise PreviewCancelled('다른 문서로 이동했습니다.')
                    self._current.move_to_end(client)
                    return old_event
                old_event.set()
            event = threading.Event()
            self._current[client] = (generation, path, event)
            self._current.move_to_end(client)
            while len(self._current) > self._clients:
                _, (_, _, retired) = self._current.popitem(last=False)
                retired.set()
            return event

    def cancel(self, client, generation):
        self._event(client, generation, None).set()

    @contextmanager
    def work(self, client=None, generation=None, path=None):
        event = self._event(client, generation, path)
        acquired = False
        try:
            while not acquired:
                if event.is_set():
                    raise PreviewCancelled('다른 문서로 이동했습니다.')
                acquired = self._slots.acquire(timeout=.025)
            if event.is_set():
                raise PreviewCancelled('다른 문서로 이동했습니다.')
            yield event.is_set
        finally:
            if acquired:
                self._slots.release()
