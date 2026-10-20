import argparse
import json
import hashlib
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/api'))
from app.evaluation import evaluate

parser = argparse.ArgumentParser()
parser.add_argument('--file', help='JSON: {source:..., cases:[{text,start,end}]}')
parser.add_argument('--output', help='Write the same JSON report to a file (may contain input questions)')
options = parser.parse_args()
dataset = None
source = 'synthetic-development'
today = date(2026, 10, 18)
dataset_name = 'synthetic-development-v2'
dataset_hash = None
if options.file:
    try:
        raw = Path(options.file).read_bytes()
        payload = json.loads(raw)
        dataset = payload['cases']
        source = payload['source']
        dataset_name = payload['dataset']
        today = date.fromisoformat(payload['date'])
        if not isinstance(source, str) or not source.strip() or not isinstance(dataset_name, str) or not dataset_name.strip():
            raise ValueError('source and dataset must be non-empty strings')
        if not isinstance(dataset, list) or not dataset:
            raise ValueError('cases must be a non-empty array')
        for case in dataset:
            if not isinstance(case, dict) or not isinstance(case['text'], str) or not 1 <= len(case['text'].strip()) <= 200:
                raise ValueError('each case needs a text of 1..200 characters')
            start, end = case['start'], case['end']
            if (start is None) != (end is None):
                raise ValueError('start and end must both be dates or both null')
            if start is not None and not 0 <= (date.fromisoformat(end) - date.fromisoformat(start)).days <= 30:
                raise ValueError('invalid expected date range')
        dataset_hash = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error('Invalid evaluation dataset (' + type(error).__name__ + '); require dataset/source/date/cases and valid text/start/end')
result = evaluate(dataset=dataset, today=today, dataset_name=dataset_name)
result['source'] = source
result['dataset_sha256'] = dataset_hash
report = json.dumps(result, ensure_ascii=True, indent=2)
if options.output:
    Path(options.output).write_text(report + '\n', encoding='utf-8')
print(report)
raise SystemExit(bool(result['failures']))
