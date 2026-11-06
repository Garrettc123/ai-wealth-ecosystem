#!/usr/bin/env python3
"""Offline Garcar Aegis evidence verification with independently saved tip.

Without an external tip anchor a database owner can rewrite or truncate a
hash chain. Do not treat this as a blockchain or immutable audit log.
"""
import argparse
import hmac
import json
from pathlib import Path

from strategies.aegis_scanner import EvidenceLedger, ResearchError


def verify_database(path: str, external_tip: str) -> dict:
    if not isinstance(external_tip, str) or len(external_tip) != 64 or any(c not in '0123456789abcdef' for c in external_tip):
        raise ResearchError('Expected tip must be 64 lowercase hex characters')
    if not Path(path).is_file():
        raise ResearchError('Evidence database not found')
    ledger = EvidenceLedger(path)
    try:
        intact = ledger.verify()
        count, tip = ledger.db.execute('SELECT COUNT(*), COALESCE((SELECT digest FROM evidence ORDER BY seq DESC LIMIT 1), ?) FROM evidence', ('0' * 64,)).fetchone()
    finally:
        ledger.close()
    anchor_match = hmac.compare_digest(tip, external_tip)
    return {'schema': 'garcar.aegis.verify.v1', 'event_count': count,
            'chain_integrity': intact, 'external_anchor_match': anchor_match,
            'verified': bool(intact and anchor_match), 'last_digest': tip,
            'live_trades_verified': False,
            'note': 'Only checks evidence integrity relative to independently retained digest, not order fills or profitability.'}


def main(argv=None):
    p = argparse.ArgumentParser(description='Verify local Aegis evidence and external digest')
    p.add_argument('--ledger', required=True)
    p.add_argument('--expected-tip', required=True, help='Digest saved outside the SQLite database')
    args = p.parse_args(argv)
    try:
        result = verify_database(args.ledger, args.expected_tip)
    except ResearchError as exc:
        p.exit(2, f'Cannot verify: {exc}\n')
    print(json.dumps(result, indent=2))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
