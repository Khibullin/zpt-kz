"""Write a new-source review without replacing the historical journal."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = root / 'procurement/data'
checkpoint = data / 'enrichment_queue_20261111.json'
cp = json.loads(checkpoint.read_text())
round_ = cp['continuation_20261112']
order = [i for b in round_['batches'] for i in b['ids']]
for result in json.load(sys.stdin):
    oid = result['offer_id']
    row = next(x for x in cp['queue'] if x['id'] == oid)
    path = data / 'enrichment_queue_items' / f"20261112-{row['supplier_id']}-{oid}.json"
    record = {**row, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'previous_journal': f"procurement/data/enrichment_queue_items/{row['supplier_id']}-{oid}.json",
              'publication_status': 'not_published', **result}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    if oid not in round_['reviewed']:
        round_['reviewed'].append(oid)
    if result.get('prepared_fitments') or result.get('prepared_photo') or result.get('universal'):
        round_['prepared'] = sorted(set(round_['prepared'] + [oid]))
    round_['next_offer_id'] = next((i for i in order if i not in round_['reviewed']), None)
    cp['checked_at'] = record['checked_at']
    checkpoint.write_text(json.dumps(cp, ensure_ascii=False, indent=2) + '\n')
    print(oid, result['status'])
