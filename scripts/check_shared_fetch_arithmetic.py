#!/usr/bin/env python3
"""Independent signed-field check using the dataset actually read by DRAM."""
import hashlib
import json
import re
import sys
from pathlib import Path

def main():
    log_path,dataset_path=map(Path,sys.argv[1:3])
    text=log_path.read_text()
    repeat=int(re.search(r'repeated_offdiagonal_blocks=(\d+)',text)[1])
    states=[0xa5a55a5a ^ (0x01010101*c) for c in range(8)]
    matrix=[[] for _ in range(256)]
    for line in dataset_path.read_text().splitlines()[1:]:
        i,j,w=map(int,line.split());i-=1;j-=1
        matrix[i].append((j,w*(repeat if i//32!=j//32 else 1)))
    outputs={(int(it),int(core)):int(value,16) for it,core,value in re.findall(
        r'CORE_RESULT iteration=(\d+) core=(\d+) state=([0-9a-f]+)',text)}
    assert len(outputs)==16, len(outputs)
    for iteration in range(2):
        spins=[1 if states[i//32]>>(i%32)&1 else -1 for i in range(256)]
        following=[0]*8
        for i,neighbors in enumerate(matrix):
            field=spins[i]+sum(w*spins[j] for j,w in neighbors)
            if field>=0:following[i//32]|=1<<(i%32)
        for c,value in enumerate(following):
            assert outputs[iteration,c]==value,(iteration,c,outputs[iteration,c],value)
        states=following
    jobs=len(re.findall(r'CORE_INT .*event=retire ',text))
    requests=len(re.findall(r'CORE_MEM .*event=request ',text))
    responses=len(re.findall(r'CORE_MEM .*event=response ',text))
    weights=len(re.findall(r'CORE_INT .*event=weight ',text))
    operand_reads=len(re.findall(r'CORE_STATE .*event=request ',text))
    assert jobs==12*repeat,(jobs,repeat)
    assert requests==responses==16*jobs and weights==32*jobs,(requests,responses,weights,jobs)
    assert operand_reads==jobs,(operand_reads,jobs)
    result=dict(status='PASS',checked_next_state_bits=512,iterations=2,
                directed_jobs=jobs,dram_requests=requests,dram_responses=responses,
                weight_beats=weights,operand_reads=operand_reads,
                repeated_offdiagonal_blocks=repeat,graph_case=repeat==1,
                dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                rtl_log_sha256=hashlib.sha256(log_path.read_bytes()).hexdigest())
    log_path.with_suffix('.arithmetic.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

if __name__=='__main__':main()
