from collections import OrderedDict
from threading import Lock

_cache_lock = Lock()
_call_id_cache: OrderedDict[str, str] = OrderedDict()
_name_cache: OrderedDict[str, str] = OrderedDict()
_last_signature: str | None = None
_max_entries = 10000

def cache_thought_signature(call_id: str | None, function_name: str | None, signature: str | None) -> None:
    global _last_signature
    if not signature:
        return
    with _cache_lock:
        _last_signature = signature
        if call_id:
            _call_id_cache[call_id] = signature
            _call_id_cache.move_to_end(call_id)
            if len(_call_id_cache) > _max_entries:
                _ = _call_id_cache.popitem(last=False)
        if function_name:
            _name_cache[function_name] = signature
            _name_cache.move_to_end(function_name)
            if len(_name_cache) > _max_entries:
                _ = _name_cache.popitem(last=False)

def get_cached_thought_signature(call_id: str | None = None, function_name: str | None = None) -> str | None:
    with _cache_lock:
        if call_id and call_id in _call_id_cache:
            return _call_id_cache[call_id]
        if function_name and function_name in _name_cache:
            return _name_cache[function_name]
        return _last_signature
