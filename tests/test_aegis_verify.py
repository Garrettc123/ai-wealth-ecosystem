import tempfile
import unittest
from pathlib import Path

from strategies.aegis_scanner import EvidenceLedger, ResearchError, sha256
from strategies.aegis_verify import verify_database


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'aegis.db')
        self.ledger = EvidenceLedger(self.path)
        self.report = {'schema': 'example', 'mode': 'test_only', 'realized_profit_usd': None}
        self.report['evidence_sha256'] = sha256(self.report)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_saved_tip_verified(self):
        tip = self.ledger.append(self.report)
        self.assertTrue(verify_database(self.path, tip)['verified'])

    def test_deleted_tail_detected_using_independent_anchor(self):
        self.ledger.append(self.report)
        tip = self.ledger.append(self.report)
        self.ledger.db.execute('DELETE FROM evidence WHERE seq=2')
        result = verify_database(self.path, tip)
        self.assertTrue(result['chain_integrity'])
        self.assertFalse(result['external_anchor_match'])
        self.assertFalse(result['verified'])

    def test_wrong_anchor_detected(self):
        self.ledger.append(self.report)
        self.assertFalse(verify_database(self.path, '0' * 64)['verified'])

    def test_malformed_anchor_denied(self):
        with self.assertRaises(ResearchError):
            verify_database(self.path, 'fake-anchor')

    def test_missing_database_denied(self):
        self.ledger.close()
        with self.assertRaises(ResearchError):
            verify_database(self.path + '.missing', '0' * 64)
        self.ledger = EvidenceLedger(self.path)


if __name__ == '__main__':
    unittest.main()
