import pickle
import os
import inspect
import warnings

from lmql.runtime.lmql_runtime import LMQLQueryFunction
import lmql
from lmql import LMQLResult, F

# cache query results by query code and arguments
global cache_file
cache_file = None
global cache
cache = None

stats = {
    "total": 0,
    "cached": 0
}

def set_cache(path):
    global cache, cache_file
    cache_file = path
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                cache = pickle.load(f)
        except:
            warnings.warn(f"warning: failed to load cache file {cache_file}")
            cache = {}
    else:
        cache = {}

def caching(state):
    global cache
    global cache_file

    if state:
        set_cache(".lmql-algorithms-cache")
    else:
        cache_file = None
        cache = None

def persist_cache():
    global cache
    global cache_file
    if cache is not None and cache_file is not None:
        with open(cache_file, "wb") as f:
            pickle.dump(cache, f)


async def apply(q, *args, **kwargs):
    global cache

    if type(q) is str:
        where = kwargs.pop("where", None)
        q = F(q, constraints=where, is_async=True)

    lmql_code = q.lmql_code

    # handle non-LMQL queries
    if type(q) is not LMQLQueryFunction and not hasattr(q, "__lmql_query_function__"):
        return await q(*args) if inspect.iscoroutinefunction(q) else q(*args)
    global stats
    stats["total"] += 1

    # get source code for q.__fct__
    try:
        # convert dict to list
        key_args = [tuple(sorted(list(a.items()))) if type(a) is dict else a for a in args]
        key_args = [tuple(a) if type(a) is list else a for a in key_args]
        key = (lmql_code, *key_args).__hash__()
        key = (lmql_code, *key_args)
    except:
        warnings.warn(
            f"warning: cannot hash LMQL query arguments {args}. Change the argument types to be hashable."
        )
        key = str(lmql_code) + str(args)

    if cache is not None and key in cache.keys():
        stats["cached"] += 1
        return cache[key]
    else:
        try:
            result = await q(*args, **kwargs)
            if len(result) == 1:
                result = result[0]
            if type(result) is LMQLResult:
                if "RESULT" in result.variables.keys():
                    result = result.variables["RESULT"].strip()

            if cache is not None:
                cache[key] = result
                persist_cache()
        except Exception as e:
            print(f"Failed for args: {args} {kwargs}", flush=True)
            raise e

        return result

def get_stats():
    global stats
    return f'lmql.algorithms Stats: Total queries: {stats["total"]}, Cached queries: {stats["cached"]}'