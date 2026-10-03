from typing import ParamSpec, TypeVar, Coroutine, Any, Callable
from asyncio import create_task, Task
from functools import wraps
from collections import OrderedDict
from time import time

from . import config


P = ParamSpec("P")
T = TypeVar("T")
AsyncFunc = Callable[P, Coroutine[Any, Any, T]]

def async_dedupe(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
    tasks: dict[int, Task[T]] = {}
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        nonlocal tasks
        key = hash((args, tuple(sorted(kwargs.items()))))
        if key in tasks:
            return await tasks[key]
        task = create_task(func(*args, **kwargs))
        tasks[key] = task
        try:
            result = await task
            return result
        finally:
            tasks.pop(key, None)
    return wrapper

CACHE_TTL = config.cache_ttl
MAXSIZE = config.cache_max_size

def func_ttl_cache(maxsize: int) -> Callable[[AsyncFunc[P, T]], AsyncFunc[P, T]]:
    def _func_ttl_cache(func: AsyncFunc[P, T]) -> AsyncFunc[P, T]:
        cache: OrderedDict[int, tuple[float, Any]] = OrderedDict()
        maxsize_ = maxsize

        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal cache
            key = hash((args, tuple(sorted(kwargs.items()))))
            now = time()

            if key in cache:
                ttl, data = cache[key]
                if ttl > now:
                    cache.move_to_end(key)
                    return data
                del cache[key]

            data = await func(*args, **kwargs)
            cache[key] = (now + CACHE_TTL, data)

            while len(cache) > maxsize_:
                cache.popitem(last=False)

            return data

        return wrapper

    return _func_ttl_cache