#!/usr/bin/env python3
"""Deterministic signed diagonal and asymmetric off-diagonal test blocks."""
import sys
from pathlib import Path


def coupling(i, j):
    if i == j:
        return 0
    i, j = sorted((i, j))
    a, r = divmod(i, 32)
    b, c = divmod(j, 32)
    if a == b:
        return (3*r + 5*c + a) % 5 - 2
    if a == 0 and b in (1, 2, 4):
        return (3*r + 5*c + a + b) % 7 - 3
    return 0


if __name__ == '__main__':
    output = Path(sys.argv[1])
    edges = [(i+1, j+1, coupling(i, j)) for i in range(256)
             for j in range(256) if coupling(i, j)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('256 0\n' + ''.join(
        f'{i} {j} {w}\n' for i, j, w in edges))
