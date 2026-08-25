import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(HERE, "engine")
sys.path.insert(0, ENGINE)
os.environ["ADS_MARKET"] = "US"
import db                # noqa: E402
import harvest_suggest   # noqa: E402


def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path


def add_design(conn, asin, title, pt="standard_tshirt", life=0):
    # a design ad group whose NAME encodes ASIN_type_Title, mapped to the ASIN
    agid = "ag_" + asin
    conn.execute("INSERT INTO ad_groups(ad_group_id,campaign_id,name,state) VALUES(?,?,?,?)",
                 (agid, "c1", f"{asin}_{pt}_{title}", "ENABLED"))
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type,lifetime_sales) VALUES(?,?,?,?)",
                 (agid, asin, pt, life))
    conn.commit()


class Tokenize(unittest.TestCase):
    def test_drops_generic_words(self):
        self.assertEqual(harvest_suggest.tokenize("grey heron t shirt"), ["grey", "heron"])
        self.assertEqual(harvest_suggest.tokenize("captain of the fleet second voyage outfit"),
                         ["captain", "fleet", "second", "voyage"])


class Suggest(unittest.TestCase):
    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def test_whole_word_not_substring(self):
        add_design(self.conn, "B1", "Grey Heron Coastal Streetwear")
        add_design(self.conn, "B2", "Blueheron Lantern Pewter Quartz Kite")
        add_design(self.conn, "B3", "Heronry Lantern Pewter Quartz Kite")
        out = harvest_suggest.suggest(self.conn, "heron t shirt")
        asins = [r["asin"] for r in out]
        self.assertIn("B1", asins)          # 'Heron' whole word
        self.assertNotIn("B2", asins)       # 'Blueheron' is a different token
        self.assertNotIn("B3", asins)       # 'Heronry' is a different token

    def test_ranks_family_and_orders_by_match_then_sales(self):
        add_design(self.conn, "B1", "Daddy of Captain Fleet Second Voyage Crew Set", life=5)
        add_design(self.conn, "B2", "Mama of Captain Fleet Second Voyage Crew Set", life=9)
        add_design(self.conn, "B3", "Some Unrelated Football Design")
        out = harvest_suggest.suggest(self.conn, "captain of the fleet second voyage outfit")
        asins = [r["asin"] for r in out]
        self.assertEqual(set(asins), {"B1", "B2"})   # B3 shares no meaningful token
        # equal score -> higher lifetime_sales first
        self.assertEqual(asins[0], "B2")

    def test_multi_underscore_product_type_does_not_leak_into_title(self):
        # product_type itself contains underscores; a naive "split on 2nd _" would
        # leave "pullover_hoodie_" glued to the front of the title, leaking the
        # word "hoodie" into the design's tokens even though the real title never
        # mentions it.
        add_design(self.conn, "B4", "Sunset Palm Trees Vacation",
                    pt="standard_pullover_hoodie")
        out = harvest_suggest.suggest(self.conn, "hoodie")
        asins = [r["asin"] for r in out]
        self.assertNotIn("B4", asins)         # no real title word matches "hoodie"
        for r in out:
            self.assertNotIn("hoodie", r["matched_words"])  # fragment never leaks


import json, subprocess

# timeout: these shell out to appctl against the REAL market database.
# With the app running, its serve workers can hold a lock, and SQLite
# waits forever by default — that is how the suite hung twice with
# nothing but an exit code to show for it. A timeout turns an
# indefinite hang into a named test failure.
SUBPROCESS_TIMEOUT = 60

class Endpoint(unittest.TestCase):
    def test_cli_returns_envelope(self):
        # smoke: unknown term -> empty suggestions, ok:true
        env = dict(os.environ, ADS_MARKET="US")
        p = subprocess.run(["python3", os.path.join(ENGINE, "appctl.py"), "harvest-suggest", "--term", "zzz-nomatch-zzz"],
                           capture_output=True, cwd=HERE, env=env, timeout=SUBPROCESS_TIMEOUT)
        d = json.loads(p.stdout.decode())
        self.assertTrue(d["ok"])
        self.assertEqual(d["data"]["count"], 0)
