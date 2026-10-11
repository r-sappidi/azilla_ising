#!/usr/bin/env python3
"""Mechanical manuscript preflight; not a substitute for submission-rule review."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ROOT/'results/manuscript_preflight_20260908')
OUT=parser.parse_args().output
OUT.mkdir(exist_ok=False)
tex=ROOT/'acmart-primary/asplos-submission.tex'
bib=ROOT/'acmart-primary/references.bib'
pdf=ROOT/'build/acmart-paper/asplos-submission.pdf'
source='\n'.join(line.split('%',1)[0] for line in tex.read_text().splitlines())
keys=re.findall(r'@\w+\s*\{\s*([^,]+)',bib.read_text())
cites={key.strip() for group in re.findall(r'\\cite\w*\{([^}]+)\}',source) for key in group.split(',')}
missing=sorted(cites-set(keys));duplicates=sorted({key for key in keys if keys.count(key)>1})
log=(pdf.parent/'asplos-submission.log').read_text()
issues=[line for line in log.splitlines() if re.search(r'(Reference|Citation).*undefined|undefined references|^!',line)]
text=subprocess.check_output(['pdftotext',str(pdf),'-']).decode()
report=dict(status='mechanical-check-pass' if not(missing or duplicates or issues) else 'review-required',
    missing_citations=missing,duplicate_bib_keys=duplicates,unresolved_reference_errors=issues,
    draft_placeholders=source.count('\\pending{'),private_home_paths_in_pdf='/home/' in text,
    pdf_info=subprocess.check_output(['pdfinfo',str(pdf)]).decode(),
    sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (tex,bib,pdf)},
    submission_ready=False,remaining=['author review','actual submission rules/page budget','pending results','final figure/claim review'])
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('pdf_info','sources')}))
assert not missing and not duplicates and not issues
