#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio, time, contextlib, logging
from collections import OrderedDict
from multiprocessing import Queue

import yaml, pymysql
from aiohttp import web
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

try:
    import logging_loki
except Exception:
    logging_loki = None

# ----------------------------
# Config & logging
# ----------------------------

def normalize_instances(cnf: dict) -> list[dict]:
    m = cnf.get("mysql", {}) or {}
    insts = m.get("instances")
    if insts:  # new format
        out = []
        for it in insts:
            if not it:
                continue
            out.append({
                "name": it.get("name") or cnf.get("name","perf_digest"),
                "host": it.get("host","localhost"),
                "port": int(it.get("port",3306)),
                "user": it.get("user"),
                "pass": it.get("pass"),
            })
        return out
    # legacy → single instance synthesized
    return [{
        "name": cnf.get("name","perf_digest"),
        "host": m.get("host","localhost"),
        "port": int(m.get("port",3306)),
        "user": m.get("user"),
        "pass": m.get("pass"),
    }]

def read_config(path: str):
    with open(path, "r") as f:
        cnf = yaml.safe_load(f)
    name = cnf.get("name","perf_digest")
    period = int(cnf.get("period",120))
    logger_name = cnf.get("logger","PerfDigest")
    listen_address = cnf.get("listen_address","0.0.0.0")
    listen_port = int(cnf.get("listen_port",3162))
    mysql = cnf.get("mysql",{}) or {}
    log_key = mysql.get("log_column","info")
    replacements = cnf.get("replacements",{}) or {}
    extra_tags_cfg = list(mysql.get("extra_tags",[]) or [])
    instances = normalize_instances(cnf)
    # Loki
    handler = None
    if logging_loki and cnf.get("loki",{}).get("url"):
        try:
            handler = logging_loki.LokiQueueHandler(
                Queue(-1),
                url=cnf["loki"]["url"],
                tags={"instance": name},
                version="1",
            )
        except Exception:
            handler = None
    # query (shared for all)
    query = mysql.get("query")
    return (cnf, name, period, logger_name, listen_address, listen_port,
            log_key, replacements, extra_tags_cfg, instances, handler, query)

(CNF, SERVICE_NAME, PERIOD, LOGGER_NAME, LISTEN_ADDRESS, LISTEN_PORT,
 LOG_KEY, REPLACEMENTS, EXTRA_TAGS_CFG, INSTANCES, LOKI_HANDLER, QUERY) = read_config("perf_digest2loki-config.yml")

logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False
if LOKI_HANDLER and not any(isinstance(h, type(LOKI_HANDLER)) for h in logger.handlers):
    logger.addHandler(LOKI_HANDLER)
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    sh = logging.StreamHandler(); sh.setLevel(logging.INFO); logger.addHandler(sh)

# ----------------------------
# Metrics
# ----------------------------

REG = CollectorRegistry()
DIGEST_UP = Gauge("digest_up","1 if last DB query succeeded, 0 otherwise",["instance"],registry=REG)
COUNTERS: dict[str, Counter] = {}

def get_counter_for(tag: str) -> Counter:
    key = tag.lower()
    if key not in COUNTERS:
        COUNTERS[key] = Counter(f"perf_digest_{key}", f"Counter of {tag}", ["instance","schema","digest"], registry=REG)
    return COUNTERS[key]

# ----------------------------
# State (LRU+TTL)
# ----------------------------

class SeenStore:
    def __init__(self, max_items=50000, ttl_sec=24*3600):
        self.max, self.ttl = max_items, ttl_sec
        self.s: OrderedDict[tuple, tuple[int,float]] = OrderedDict()
    def get(self, k):
        v = self.s.get(k);
        if not v: return None
        val, ts = v
        if time.time()-ts > self.ttl:
            self.s.pop(k, None); return None
        self.s.move_to_end(k, last=True); return val
    def set(self, k, val: int):
        self.s[k]=(val,time.time()); self.s.move_to_end(k, last=True)
        while len(self.s)>self.max: self.s.popitem(last=False)

SEEN = SeenStore()

# ----------------------------
# Helpers
# ----------------------------

def apply_replacements(s: str) -> str:
    if not REPLACEMENTS: return s
    for old,new in REPLACEMENTS.items(): s = s.replace(old,new)
    return s

def build_extra_tags(row: dict, cfg: list[str]) -> dict:
    extra = {"severity":"info"}
    for t in cfg or []:
        if t in row and row[t] is not None: extra[t]=str(row[t])
    return extra

def log_to_loki(stmt: str, tags: dict):
    if not logger.handlers: return
    retries=0
    while True:
        try:
            logger.error(stmt, extra={"tags": tags}); break
        except ValueError:
            retries+=1
            if retries>10: break
            time.sleep(1.0)

def _fetch_rows_blocking(host,user,pw,port,query):
    conn=None
    try:
        conn=pymysql.connect(host=host,user=user,password=pw,port=port,
                             database=None,cursorclass=pymysql.cursors.DictCursor,
                             connect_timeout=10,read_timeout=30,write_timeout=30,autocommit=True)
        with conn.cursor() as cur:
            cur.execute(query)
            rows=[];
            while True:
                chunk=cur.fetchmany(1000)
                if not chunk: break
                rows.extend(chunk)
            return rows
    finally:
        with contextlib.suppress(Exception):
            if conn: conn.close()

async def run_probe_once(inst: dict, *, override_host: str|None=None) -> tuple[bool,int]:
    if not QUERY or not inst.get("user") or not inst.get("pass"):
        return False,0
    host = override_host or inst["host"]
    try:
        rows = await asyncio.to_thread(_fetch_rows_blocking, host, inst["user"], inst["pass"], int(inst["port"]), QUERY)
        processed=0
        for row in rows:
            if not row or LOG_KEY not in row or row[LOG_KEY] is None: continue
            stmt = apply_replacements(str(row[LOG_KEY]))
            schema = str(row.get("SCHEMA_NAME",""))
            digest = str(row.get("DIGEST",""))[:10] if row.get("DIGEST") else ""
            # counters: deltas for COUNT_/SUM_
            extra_tags = build_extra_tags(row, EXTRA_TAGS_CFG)
            for tag in (EXTRA_TAGS_CFG or []):
                if tag.startswith(("COUNT_","SUM_")):
                    try: val=int(row.get(tag,0))
                    except Exception: val=0
                    key=(inst["name"], schema, digest, tag)
                    prev = SEEN.get(key); delta = 0 if prev is None else max(0, val-prev)
                    SEEN.set(key,val)
                    if delta>0:
                        get_counter_for(tag).labels(inst["name"], schema, digest).inc(delta)
                    extra_tags[f"DIFF_{tag}"]=str(delta)
            if stmt:
                # add instance tag to loki explicitly (loki handler has service-wide tag, but we want per-DB)
                tags = {"instance": inst["name"], **extra_tags}
                log_to_loki(stmt, tags)
            processed+=1
        return True, processed
    except Exception:
        return False,0

# ----------------------------
# HTTP
# ----------------------------

async def handle_metrics(request: web.Request):
    return web.Response(body=generate_latest(REG), content_type=CONTENT_TYPE_LATEST)

async def handle_root(request: web.Request):
    return web.Response(text="See /metrics and /probe?instance=<name>&target=<host>\n")

def find_instance_by_name(name: str) -> dict|None:
    for it in INSTANCES:
        if it["name"] == name: return it
    return None

async def handle_probe(request: web.Request):
    name = request.query.get("instance") or (INSTANCES[0]["name"] if INSTANCES else SERVICE_NAME)
    inst = find_instance_by_name(name) or (INSTANCES[0] if INSTANCES else None)
    if not inst:
        return web.json_response({"ok":False,"error":"no instances configured"}, status=500)
    target = request.query.get("target")  # optional override
    ok, cnt = await run_probe_once(inst, override_host=target)
    DIGEST_UP.labels(inst["name"]).set(1 if ok else 0)
    return web.json_response({"ok":ok,"rows_processed":cnt,"instance":inst["name"],"target":target or inst["host"]},
                             status=200 if ok else 500)

# ----------------------------
# Periodic
# ----------------------------

async def periodic_task(app: web.Application):
    period = max(5,int(PERIOD))
    while True:
        # run all configured instances concurrently
        tasks = [run_probe_once(inst) for inst in INSTANCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for inst,res in zip(INSTANCES,results):
            ok = isinstance(res, tuple) and res[0] is True
            DIGEST_UP.labels(inst["name"]).set(1 if ok else 0)
        await asyncio.sleep(period)

async def on_startup(app: web.Application):
    # pre-create label series
    for inst in INSTANCES:
        DIGEST_UP.labels(inst["name"]).set(0)
    app["periodic_task"]=asyncio.create_task(periodic_task(app))

async def on_cleanup(app: web.Application):
    t=app.get("periodic_task")
    if t:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t

def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.add_routes([web.get("/",handle_root), web.get("/metrics",handle_metrics), web.get("/probe",handle_probe)])
    web.run_app(app, host=LISTEN_ADDRESS, port=LISTEN_PORT)

if __name__ == "__main__":
    asyncio.run(main())
