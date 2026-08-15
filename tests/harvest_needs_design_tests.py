import os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "engine")); os.environ["ADS_MARKET"] = "US"
import db      # noqa: E402
import appctl  # noqa: E402

def temp_conn():
    fd, path = tempfile.mkstemp(suffix=".sqlite"); os.close(fd)
    real = db.DB_PATH; db.DB_PATH = path
    conn = db.connect(); db.DB_PATH = real
    return conn, path

def add_winner(conn, term, src_ag, promoted=0):
    conn.execute("""INSERT INTO harvest_log(search_term,source_ad_group_id,kind,product_type,
                    source_campaign_id,cpc,promoted) VALUES(?,?,?,?,?,?,?)""",
                 (term, src_ag, "keyword", "standard_tshirt", "c1", 0.2, promoted))
    conn.commit()

def add_agp(conn, agid, asin):
    conn.execute("INSERT INTO ad_group_product(ad_group_id,asin,product_type) VALUES(?,?,?)",
                 (agid, asin, "standard_tshirt")); conn.commit()

class NeedsDesign(unittest.TestCase):
    def setUp(self): self.conn, self.path = temp_conn()
    def tearDown(self): self.conn.close(); os.unlink(self.path)

    def test_flags(self):
        add_agp(self.conn, "cohort1", None)     # cohort: asin NULL
        add_agp(self.conn, "design1", "B1")     # single design
        add_winner(self.conn, "cohort term", "cohort1", promoted=0)
        add_winner(self.conn, "design term", "design1", promoted=0)
        add_winner(self.conn, "cohort promoted", "cohort1", promoted=1)
        rows = {w["search_term"]: w for w in appctl._harvest_winners(self.conn)}
        self.assertTrue(rows["cohort term"]["needs_design"])
        self.assertFalse(rows["design term"]["needs_design"])
        self.assertFalse(rows["cohort promoted"]["needs_design"])
