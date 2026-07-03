# deps: pip install duckdb pandas msgpack
import msgpack
import json
import importlib
from cisei_lib.cli.usermng.home_folder import UserHome
import cisei_lib.cli.importer.gdf_transformer as gdf_tr
import duckdb, hashlib
from importlib import resources
from pathlib import Path

def ensure_base_schema(con, base_sql_path):
    exists = con.execute("""
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema='core' AND table_name='metrics_long'
        LIMIT 1
    """).fetchone()
    if not exists:
        con.execute(open(base_sql_path, "r", encoding="utf-8").read())

def ensure_views_once(con, views_sql_path, name="views.sql"):
    sql_bytes = open(views_sql_path, "rb").read()
    digest = hashlib.sha256(sql_bytes).hexdigest()

    con.execute("""
        CREATE SCHEMA IF NOT EXISTS meta;
        CREATE TABLE IF NOT EXISTS meta.sql_applied(
          name TEXT PRIMARY KEY,
          hash TEXT
        )
    """)
    row = con.execute("SELECT hash FROM meta.sql_applied WHERE name = ?", [name]).fetchone()
    if not row or row[0] != digest:
        con.execute(sql_bytes.decode("utf-8"))              # your CREATE OR REPLACE VIEW ... statements
        if row:
            con.execute("UPDATE meta.sql_applied SET hash=? WHERE name=?", [digest, name])
        else:
            con.execute("INSERT INTO meta.sql_applied(name, hash) VALUES (?,?)", [name, digest])
        return True
    return False


class consumeMetrics:

    def __init__(self, network: str, home: UserHome, **kwargs):
        self.home = home
        self.network = network

        DEFAULT_CONTEXT = {
            'in_file': 'host_items.msgpack',
            'out_file': 'metrics.duckdb',
            'sql_base': 'schema_base.sql',
            'sql_views': 'views.sql',
            'delete': False,
        }
        self.context = {**DEFAULT_CONTEXT, **kwargs}
        self.config = self.home.get_monitoring_path(self.network, 'configuration')
        self.in_path = self.home.get_monitoring_path(self.network, 'inbox', self.context['in_file'])
        self.out_path = self.home.get_monitoring_path(self.network, 'current', self.context['out_file'])
        self.sql_views_path = (
            self.home.get_monitoring_path(self.network, 'configuration') / 'sql' / self.context['sql_views']
        )

        self.compiled: list | None = None
        self.conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn is not None:
            try:
                self.conn.close()
            finally:
                self.conn = None
        # Don’t suppress exceptions → return False
        return False

#----------------------------------------------------------------------
# PUBLIC METHODS

    # Create a single shared connection for all methods in the class
    def open_database(self):
        """Open connection and ensure base schema exists."""
        if self.conn is None:
            self.conn = duckdb.connect(str(self.out_path))
            self._ensure_base_schema(self.conn)
        return self.conn
 
    # Compile lambda rules from TOML for renaming and tranforming values before importing
    def add_rules(self):
            
        rules, meta = gdf_tr.load_rules_from_dir(self.config, rule_type = 'metrics')

        # pre-compile/capture callables once
        self.compiled = []
        for rule in rules:
            try:
                f = self._get_callable(rule)
                self.compiled.append((rule, f))
            except Exception as e:
                # bad rule → skip entirely
                print(f"rule {rule.get('dst')} ignored: {type(e).__name__}: {e}")

    #TODO: include consumption of multiple files since last timestamp processed
    def load_metrics(self):

        with open(self.in_path, "rb") as f:
            nested = msgpack.unpack(f, raw=False)

        return nested

        # rename keys and transform values before saving in the database
    
    # rename keys and transform values before saving in the database
    def apply_rules(self, nested):
        """Apply compiled rules to raw nested metrics. 
        Each rule has {src: [...], dst: str} and a function f(*vals) -> value."""

        res = {}

        def first(x):
            # [v, ts] -> v ; v -> v
            return x[0] if isinstance(x, (list, tuple)) else x

        def get_ts(x):
            # extract timestamp from [v, ts]
            if isinstance(x, (list, tuple)) and len(x) > 1:
                t = x[1]
                try:
                    return int(t) if isinstance(t, (int, float)) else int(float(str(t).strip()))
                except Exception:
                    return None
            return None

        for host, metrics in (nested or {}).items():
            if not isinstance(metrics, dict):
                continue
            res[host] = {}

            for rule, f in self.compiled or []:  # avoid crash if compiled is None
                try:
                    src = rule.get('src', [])
                    series = []
                    for k in src:
                        s = metrics.get(k)
                        if isinstance(s, list):
                            series.append(s)
                        else:
                            series = []
                            break
                    if not series:
                        continue

                    out = []
                    for row in zip(*series):         # align by position
                        vals = [first(cell) for cell in row]
                        ts_ref = get_ts(row[0])      # timestamp from first src
                        out.append([f(*vals), ts_ref])

                    res[host][rule['dst']] = out

                except Exception as e:
                    # soft-fail: ignore malformed rule or data
                    print(f"{host} {rule.get('src')} ignored: {type(e).__name__}: {e}")
                    continue

        return res

    # save the metrics from nested dict ({host: { metric: [ [val, ts], ... ] }})
    def save_metrics(self, nested):
        """Transform nested dict into long rows and insert into DuckDB using self.conn."""

        if self.conn is None:
            raise RuntimeError("Database connection is not open. Call open_database() first.")

        def _unwrap_series(series):
            """Return (values_iterable, metadata_dict) from a series entry."""
            if isinstance(series, dict):
                meta = {
                    "device_id": series.get("device_id"),
                    "metric_type": series.get("metric_type"),
                }
                values = (
                    series.get("values")
                    or series.get("series")
                    or series.get("data")
                    or series.get("rows")
                    or []
                )
                return values, meta
            return series, {}

        def to_long_rows(res):
            """Yield rows shaped for core.metrics_long inserts."""
            for host, metrics in (res or {}).items():
                if not isinstance(metrics, dict):
                    continue

                host_meta = metrics.get("__meta__") if isinstance(metrics.get("__meta__"), dict) else {}

                for metric, series in metrics.items():
                    if metric == "__meta__":
                        continue

                    values, metric_meta = _unwrap_series(series)
                    if not isinstance(values, (list, tuple)):
                        continue

                    base_device = metric_meta.get("device_id") or host_meta.get("device_id") or host
                    base_type = metric_meta.get("metric_type") or host_meta.get("metric_type")

                    for pair in values:
                        raw_val = raw_ts = raw_state = None

                        if isinstance(pair, dict):
                            raw_val = pair.get("value")
                            raw_ts = pair.get("ts")
                            raw_state = pair.get("state") or pair.get("state_val")
                        elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                            raw_val, raw_ts = pair[0], pair[1]
                        else:
                            continue

                        try:
                            ts = int(raw_ts) if raw_ts is not None else None
                        except Exception:
                            continue
                        if ts is None:
                            continue

                        metric_type = base_type
                        value = state_val = None

                        if metric_type == "event":
                            state_val = raw_state if raw_state is not None else (None if raw_val is None else str(raw_val))
                        else:
                            try:
                                value = float(raw_val) if raw_val is not None else None
                                metric_type = metric_type or "measurement"
                            except Exception:
                                metric_type = "event"
                                state_val = None if raw_val is None else str(raw_val)

                        device_id = base_device or host
                        if device_id is None:
                            device_id = host

                        yield (host, device_id, metric, metric_type or "measurement", ts, value, state_val)

        rows = list(to_long_rows(nested))

        if not rows:
            return 0

        try:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """
                INSERT INTO core.metrics_long (host, device_id, metric, metric_type, ts, value, state_val)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                rows
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        return len(rows)
    # Standard pipeline for importing metrics to long format
    def run_import_metrics(self):
        """Standard pipeline: open DB, apply rules, and save processed metrics."""
        self.open_database()     # ensure DB + schema
        self.add_rules()         # must populate self.compiled
        nested = self.load_metrics()
        nested = self.apply_rules(nested)
        if nested:               # avoid unnecessary insert if empty
            self.save_metrics(nested)

    def import_scada_metrics(self, scada_db_path: Path | str) -> int:
        """Merge SCADA metrics from another DuckDB file into core.metrics_long."""
        scada_path = Path(scada_db_path)
        if not scada_path.exists():
            raise FileNotFoundError(f"SCADA database not found: {scada_path}")

        self.open_database()
        scada_rows = duckdb.connect(str(scada_path)).execute("SELECT COUNT(*) FROM core.metrics_long").fetchone()[0]
        print(f"SCADA source rows: {scada_rows}")
        ami_mapping = self._load_da_mapping()
        if not ami_mapping:
            return 0


        mapping_rows = [(device_id, host) for device_id, host in ami_mapping.items()]
        self.conn.execute("CREATE OR REPLACE TEMP TABLE tmp_scada_ami(device_id TEXT PRIMARY KEY, host TEXT)")
        self.conn.executemany(
            "INSERT INTO tmp_scada_ami(device_id, host) VALUES (?, ?)",
            mapping_rows,
        )

        inserted = 0
        attached = False
        try:
            scada_sql_path = str(scada_path).replace("'", "''")
            self.conn.execute(f"ATTACH '{scada_sql_path}' AS scada (READ_ONLY)")            
            attached = True

            match_count = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM scada.core.metrics_long AS s
                JOIN tmp_scada_ami AS m ON m.device_id = s.device_id
                """
            ).fetchone()[0]

            if match_count:
                self.conn.execute("BEGIN")
                try:
                    self.conn.execute(
                        """
                        INSERT OR REPLACE INTO core.metrics_long (host, device_id, metric, metric_type, ts, value, state_val)
                        SELECT
                            COALESCE(m.host, s.host) AS host,
                            s.device_id,
                            s.metric,
                            COALESCE(s.metric_type, 'event') AS metric_type,
                            s.ts,
                            s.value,
                            s.state_val
                        FROM scada.core.metrics_long AS s
                        JOIN tmp_scada_ami AS m ON m.device_id = s.device_id
                        """
                    )
                    self.conn.execute("COMMIT")
                    inserted = match_count
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise
        finally:
            if attached:
                self.conn.execute("DETACH scada")
            self.conn.execute("DROP TABLE IF EXISTS tmp_scada_ami")

        return inserted


    def build_bin_ref(self, bin_size: int, *, start_ts: int, end_ts: int, replace_existing: bool = False ) -> None:
        if self.conn is None:
            raise RuntimeError("Database is not open. Call open_database() first.")
        if end_ts <= start_ts:
            raise ValueError("end_ts must be greater than start_ts.")

        if replace_existing:
            self.conn.execute(
                "DELETE FROM calc.bin_ref WHERE bin_size = ?",
                [bin_size],
            )

        self.conn.execute(
            """
            INSERT OR REPLACE INTO calc.bin_ref (ts_bin, bin_size)
            SELECT ts_bin, bin_size
            FROM make_bins(?, ?, ?)
            """,
            [start_ts, end_ts, bin_size],
        )



    # Binarize all metrics available according to bin_ref and bin_size
    def build_metrics_bins(self, bin_size: int):
        """
        Populate calc.metrics_bins for a given bin_size using the metrics_bins() macro.

        Args:
            bin_size (int): bin width in seconds (e.g. 300 = 5min, 1800 = 30min).
        """
        if self.conn is None:
            raise RuntimeError("Database is not open. Call open_database() first.")

        sql = f"""
            INSERT OR REPLACE INTO calc.metrics_bins
            SELECT * FROM metrics_bins({bin_size});
        """
        self.conn.execute(sql)


    def build_events_bins(self, bin_size: int):
        sql = f"""
            INSERT OR REPLACE INTO calc.events_bins
            SELECT * FROM events_bins({bin_size});
        """
        self.conn.execute(sql)



#---------------------------------------------------------------------
# PRIVATE METHODS

    def _load_device_mapping(self, key: str) -> dict[str, str]:
        """Return device_id → host mapping using a specific key from net_graph.json."""
        mapping: dict[str, str] = {}
        graph_path = Path(
            self.home.get_monitoring_path(self.network, "current", "net_graph.json")
        )

        try:
            raw = graph_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return mapping

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return mapping

        for node in payload.get("nodes", []):
            device = (node.get(key) or "").strip()
            host = (node.get("id") or node.get("name") or "").strip()
            if device and host:
                mapping[device] = host

        return mapping

    def _load_ami_mapping(self) -> dict[str, str]:
        return self._load_device_mapping("id_ami")

    def _load_da_mapping(self) -> dict[str, str]:
        return self._load_device_mapping("id_da")

    def _ensure_base_schema(self, con: duckdb.DuckDBPyConnection):
        exists = con.execute("""
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='core' AND table_name='metrics_long'
            LIMIT 1
        """).fetchone()
        if exists:
            return

        sql_text = resources.files("cisei_lib.sql").joinpath(self.context["sql_base"]).read_text()
        con.execute(sql_text)

    
    def _get_callable(self, rule):
        # Supports: function='lambda' + expression='a,b: ...'
        #        or function='mylib.retry_rate' (expression ignored)
        fn_spec = rule.get('function', 'lambda')
        if fn_spec == 'lambda':
            f_literal = 'lambda ' + rule.get('expression', 'a: a')
            return eval(f_literal, {"__builtins__": {}})
        # module.function
        mod_name, func_name = fn_spec.rsplit('.', 1)
        mod = importlib.import_module(mod_name)
        return getattr(mod, func_name)
    

    def _drop_calc_views(self,con):
        """
        Drop all views in the 'calc' schema.
        """
        rows = con.execute("""
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'calc'
        """).fetchall()

        for (name,) in rows:
            con.execute(f'DROP VIEW IF EXISTS calc."{name}"')

    def _ensure_views(self, con):
        sql_bytes = open(self.sql_views_path, "rb").read()
        digest = hashlib.sha256(sql_bytes).hexdigest()

        con.execute("""
            CREATE SCHEMA IF NOT EXISTS meta;
            CREATE TABLE IF NOT EXISTS meta.sql_applied(
            name TEXT PRIMARY KEY,
            hash TEXT
            )
        """)
        row = con.execute("SELECT hash FROM meta.sql_applied WHERE name = ?", [self.context['sql_views']]).fetchone()
        if not row or row[0] != digest:
            self._drop_calc_views(con)
            con.execute(sql_bytes.decode("utf-8"))              # your CREATE OR REPLACE VIEW ... statements          
            if row:
                con.execute("UPDATE meta.sql_applied SET hash=? WHERE name=?", [digest, self.context['sql_views']])
            else:
                con.execute("INSERT INTO meta.sql_applied(name, hash) VALUES (?,?)", [self.context['sql_views'], digest])
            con.execute("CHECKPOINT")
            con.execute("VACUUM")
            return True
        return False
    

    def update_schema(self):

        try:
            con = duckdb.connect(self.out_path)
            # your SQL library should create core schema/table/views
            changed = self._ensure_base_schema(con)
            changed = self._ensure_views(con) or changed

            if changed:
                con.execute("CHECKPOINT")
                con.execute("VACUUM")                
        finally:
            con.close()
        
        return changed
        

#---------------------------------------------------------------------
if __name__ == '__main__':

    pass