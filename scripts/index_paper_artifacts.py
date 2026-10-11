#!/usr/bin/env python3
"""Inventory locally retained paper data without treating it as verified."""
from __future__ import annotations
import json,subprocess
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CAMPAIGNS={
 'regenerated_canonical_sparse':'results/regeneration_20260904_v2/canonical_sparse',
 'regenerated_fixed_modes':'results/regeneration_20260904_v2/execution_modes',
 'regenerated_hybrid_modes':'results/regeneration_20260904_v2/execution_modes_hybrid',
 'regenerated_mapping_comparison':'results/regeneration_20260904_v2/mapping_comparison',
 'regenerated_mapped_modes':'results/regeneration_20260904_v2/mapped_execution_modes',
 'regenerated_hybrid_oracle':'results/regeneration_20260904_v2/hybrid_oracle',
 'regenerated_dynamic_hybrid':'results/regeneration_20260904_v2/dynamic_hybrid',
 'regenerated_interconnect':'results/regeneration_20260904_v2/interconnect',
 'regenerated_exact_modes':'results/regeneration_20260904_v2/exact_execution_modes',
 'regenerated_dense_vcs':'results/regeneration_20260904_v2/dense_vcs_fixed_h1',
 'paper_final_exact_cir_scaling':'results/paper_final_20260906/exact_cir_scaling',
 'paper_final_exact_execution_modes':'results/paper_final_20260906/exact_execution_modes',
 'paper_final_exact_interconnect':'results/paper_final_20260906/exact_interconnect',
 'paper_final_exact_mapping':'results/paper_final_20260906/exact_mapping',
 'frozen_benchmarks':'results/paper_benchmarks/datasets',
 'calibrated_execution_modes':'results/paper_benchmarks/execution_modes_gset',
 'exact_execution_modes':'results/paper_benchmarks/exact_execution_modes',
 'hybrid_oracle':'results/paper_benchmarks/hybrid_oracle',
 'online_dynamic_hybrid':'results/paper_benchmarks/dynamic_hybrid',
 'batch_hybrid_tuning':'results/paper_benchmarks/batch_hybrid_tuning',
 'batch_hybrid_heldout':'results/paper_benchmarks/batch_hybrid_heldout',
 'sparse_geometry':'results/paper_benchmarks/sparse_geometry',
 'mapping_comparison':'results/paper_benchmarks/mapping_comparison',
 'interconnect_sensitivity':'results/paper_benchmarks/interconnect_sensitivity',
 'dense_calibrated_dse':'results/cir_evaluation/calibrated_dense',
 'dense_vcs_scale':'results/dense_config_search/rtl_vcs_fixed_h1_to_1m',
 'sparse_exact_64k':'results/cir_evaluation/exact_sparse/random_d25_n65536',
}
FIGURES={
 'hybrid_oracle':'plots/paper/hybrid_oracle','dynamic_hybrid':'plots/paper/dynamic_hybrid',
 'sparse_geometry':'plots/paper/sparse_geometry','dense_results':'dense_results',
 'paper_final_exact_execution_modes':'plots/paper_final_20260906/exact_execution_modes',
 'paper_final_dense_vcs':'plots/paper_final_20260906/dense_vcs_fixed_h1',
}
def campaign(path):
 p=ROOT/path;files=[x for x in p.rglob('*') if x.is_file()] if p.exists() else []
 accuracy=Counter()
 for f in files:
  if f.suffix=='.json' and ('summary' in f.name or f.name=='point.json'):
   try:
    d=json.loads(f.read_text());label=d.get('accuracy')
    if label:accuracy[label]+=1
   except (OSError,json.JSONDecodeError):pass
 return {'path':path,'exists':p.exists(),'files':len(files),'csv_files':sum(f.suffix=='.csv' for f in files),'json_files':sum(f.suffix=='.json' for f in files),'pdf_files':sum(f.suffix=='.pdf' for f in files),'png_files':sum(f.suffix=='.png' for f in files),'accuracy_labels':dict(accuracy),'latest_mtime':max((f.stat().st_mtime for f in files),default=None)}
def main():
 out=ROOT/'results/paper_benchmarks';out.mkdir(parents=True,exist_ok=True)
 try:commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,stdout=subprocess.PIPE,check=True).stdout.strip()
 except subprocess.SubprocessError:commit='unknown'
 index={'generated_at':datetime.now(timezone.utc).isoformat(),'git_commit_at_index_time':commit,'retention_warning':'Generated results and plots are ignored by Git. This inventory confirms local presence only; it is not an external backup and does not upgrade fidelity labels.','campaigns':{k:campaign(v) for k,v in CAMPAIGNS.items()},'figure_directories':{k:campaign(v) for k,v in FIGURES.items()}}
 (out/'PAPER_ARTIFACT_INDEX.json').write_text(json.dumps(index,indent=2)+'\n')
 lines=['# Local Paper Artifact Index','',index['retention_warning'],'','| Campaign | Files | CSV | JSON | Accuracy labels |','|---|---:|---:|---:|---|']
 for name,row in index['campaigns'].items():lines.append(f"| `{name}` | {row['files']} | {row['csv_files']} | {row['json_files']} | {', '.join(f'{k}: {v}' for k,v in row['accuracy_labels'].items()) or 'not indexed'} |")
 lines+=['','## Figure directories','']
 for name,row in index['figure_directories'].items():lines.append(f"- `{name}`: {row['pdf_files']} PDF, {row['png_files']} PNG (`{row['path']}`)")
 (out/'PAPER_ARTIFACT_INDEX.md').write_text('\n'.join(lines)+'\n')
 print(out/'PAPER_ARTIFACT_INDEX.json');print(out/'PAPER_ARTIFACT_INDEX.md')
if __name__=='__main__':main()
