import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect('turb.sqlite3')
cur = conn.cursor()
cur.execute('SELECT doc_number, uned_identifier, email, status, forwarding_enabled FROM uned_accounts')
db_rows = cur.fetchall()
conn.close()

db_docs = {r[0]: r for r in db_rows if r[0]}
db_uids = {r[1]: r for r in db_rows if r[1]}
print(f'Total in turb.sqlite3 uned_accounts: {len(db_rows)} (Unique docs: {len(db_docs)})')

files = [
    'data/uned_accounts.json',
    'data/uned_identities.json',
    'scratch/new_30_parsed.json',
    'scratch/verified_35_identities.json'
]

all_sources = {}
for fp in files:
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                print(f'{fp}: {len(data)} items')
                for item in data:
                    doc = item.get('doc_number') or item.get('document_number') or item.get('dni')
                    if doc:
                        all_sources.setdefault(doc, []).append((fp, item))
    except Exception as e:
        print(f'{fp}: error {e}')

print(f'\nTotal unique document numbers across all JSON files: {len(all_sources)}')
unregistered = []
for doc, occurrences in all_sources.items():
    if doc not in db_docs:
        unregistered.append((doc, occurrences))

print(f'Unregistered document numbers: {len(unregistered)}')
for doc, occs in unregistered:
    src_file, item = occs[0]
    name = item.get('full_name') or f"{item.get('name', '')} {item.get('surname1', '')}"
    print(f'  Doc: {doc} | Name: {name} | Source: {src_file}')
