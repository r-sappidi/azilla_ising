from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import torch


@dataclass
class TiledGraph:
    """Compressed upper-triangular tiled representation."""

    tiles: torch.Tensor          # [num_nonzero_tiles, M, M]
    coords: torch.Tensor         # [num_nonzero_tiles, 2], [tile_row, tile_col]
    perm: torch.Tensor           # [V], perm[new_index] = old_index
    inverse_perm: torch.Tensor   # [V], inverse_perm[old_index] = new_index
    macro_labels: torch.Tensor   # [V] labels in original vertex numbering


@dataclass
class SparseBlockGraph:
    """Permutation plus occupied block coordinates without dense tile data.

    This is the scalable representation used by Azilla timing studies.  It
    deliberately avoids both the ``V x V`` adjacency matrix and materialized
    ``M x M`` tiles when only placement and communication timing are needed.
    """

    coords: torch.Tensor         # [num_nonzero_blocks, 2]
    perm: torch.Tensor           # [V], perm[new_index] = old_index
    inverse_perm: torch.Tensor   # [V], inverse_perm[old_index] = new_index
    macro_labels: torch.Tensor   # [V] labels in original vertex numbering


def _check_cuda_tensor(x: torch.Tensor, require_cuda: bool) -> None:
    if require_cuda and not x.is_cuda:
        raise ValueError("GPU acceleration requested, but the input tensor is not on CUDA.")


def upper_triangular_to_edges(
    adjacency: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Convert an upper-triangular dense adjacency matrix into an edge list.

    Returns src, dst, weight with src <= dst. Zero entries are omitted.
    Self-loops are retained if present.
    """
    if adjacency.ndim != 2 or adjacency.shape[0] != adjacency.shape[1]:
        raise ValueError("adjacency must be square")

    rc = torch.nonzero(adjacency, as_tuple=False)
    if rc.numel() == 0:
        empty_i = torch.empty(0, dtype=torch.long, device=adjacency.device)
        empty_w = torch.empty(0, dtype=adjacency.dtype, device=adjacency.device)
        return empty_i, empty_i.clone(), empty_w

    r, c = rc[:, 0], rc[:, 1]
    lo = torch.minimum(r, c)
    hi = torch.maximum(r, c)
    w = adjacency[r, c]
    return lo.long(), hi.long(), w


def _make_symmetric_edges(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Expand upper-triangular edges into directed symmetric edges for scoring."""
    non_diag = src != dst
    s = torch.cat([src, dst[non_diag]])
    d = torch.cat([dst, src[non_diag]])
    w = torch.cat([weight, weight[non_diag]])
    return s, d, w


def _balanced_initial_labels(
    num_vertices: int,
    num_parts: int,
    device: torch.device,
    generator: Optional[torch.Generator],
) -> torch.Tensor:
    if num_vertices % num_parts != 0:
        raise ValueError("num_vertices must be divisible by num_parts")
    part_size = num_vertices // num_parts
    perm = torch.randperm(num_vertices, device=device, generator=generator)
    labels = torch.empty(num_vertices, dtype=torch.long, device=device)
    labels[perm] = torch.arange(num_vertices, device=device) // part_size
    return labels


def _neighbor_part_scores(
    sym_src: torch.Tensor,
    sym_dst: torch.Tensor,
    sym_weight: torch.Tensor,
    labels: torch.Tensor,
    num_parts: int,
    num_vertices: int,
) -> torch.Tensor:
    """scores[v, p] = total edge weight from v to vertices currently in p."""
    scores = torch.zeros(
        num_vertices * num_parts,
        dtype=sym_weight.dtype,
        device=labels.device,
    )
    flat_idx = sym_src * num_parts + labels[sym_dst]
    scores.scatter_add_(0, flat_idx, sym_weight)
    return scores.view(num_vertices, num_parts)


def _internal_weight(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    labels: torch.Tensor,
) -> torch.Tensor:
    if src.numel() == 0:
        return torch.zeros((), dtype=weight.dtype, device=labels.device)
    mask = labels[src] == labels[dst]
    return weight[mask].sum()


@torch.no_grad()
def balanced_partition_gpu(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    num_vertices: int,
    num_parts: int,
    *,
    iterations: int = 8,
    candidate_swaps: int = 64,
    seed: Optional[int] = None,
) -> torch.Tensor:
    """
    Equal-size graph partitioning heuristic designed for CUDA tensors.

    It starts from an exactly balanced random partition and performs pairwise
    swaps. Every move preserves exact partition cardinality. The expensive
    neighbor/partition scoring is done with GPU scatter operations.

    Objective: increase total edge weight whose two endpoints lie in the same
    partition (equivalently, heuristically reduce the cut).
    """
    if num_parts <= 0 or num_vertices % num_parts != 0:
        raise ValueError("num_parts must divide num_vertices")
    if num_parts == 1:
        return torch.zeros(num_vertices, dtype=torch.long, device=src.device)

    device = src.device
    if dst.device != device or weight.device != device:
        raise ValueError("src, dst, and weight must be on the same device")

    gen = None
    if seed is not None:
        gen = torch.Generator(device=device)
        gen.manual_seed(seed)

    labels = _balanced_initial_labels(num_vertices, num_parts, device, gen)
    sym_src, sym_dst, sym_weight = _make_symmetric_edges(src, dst, weight)

    best_obj = _internal_weight(src, dst, weight, labels)

    for _ in range(iterations):
        before = labels.clone()
        scores = _neighbor_part_scores(
            sym_src, sym_dst, sym_weight, labels, num_parts, num_vertices
        )

        # Pairwise swaps preserve exact balance. Scores are intentionally held
        # fixed during one sweep; the next outer iteration refreshes them.
        for p in range(num_parts):
            for q in range(p + 1, num_parts):
                vp = torch.nonzero(labels == p, as_tuple=False).flatten()
                vq = torch.nonzero(labels == q, as_tuple=False).flatten()
                if vp.numel() == 0 or vq.numel() == 0:
                    continue

                gain_p = scores[vp, q] - scores[vp, p]
                gain_q = scores[vq, p] - scores[vq, q]

                k = min(candidate_swaps, vp.numel(), vq.numel())
                if k <= 0:
                    continue

                gp, ip = torch.topk(gain_p, k=k, largest=True, sorted=True)
                gq, iq = torch.topk(gain_q, k=k, largest=True, sorted=True)
                total_gain = gp + gq
                good = total_gain > 0
                if not torch.any(good):
                    continue

                a = vp[ip[good]]
                b = vq[iq[good]]
                labels[a] = q
                labels[b] = p

        new_obj = _internal_weight(src, dst, weight, labels)
        if new_obj + 1e-12 < best_obj:
            labels = before
            break
        if torch.isclose(new_obj, best_obj):
            break
        best_obj = new_obj

    return labels


@torch.no_grad()
def _group_affinity_order(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    labels: torch.Tensor,
    num_groups: int,
) -> torch.Tensor:
    """
    Order groups so strongly connected groups tend to be adjacent.

    This does not change tile count; it improves spatial locality of occupied
    blocks. The affinity matrix and selection operations remain on the device.
    """
    device = labels.device
    if num_groups <= 1:
        return torch.arange(num_groups, device=device)

    g1 = labels[src]
    g2 = labels[dst]
    flat = g1 * num_groups + g2
    aff = torch.zeros(num_groups * num_groups, dtype=weight.dtype, device=device)
    aff.scatter_add_(0, flat, weight)
    non_diag = g1 != g2
    flat_rev = g2[non_diag] * num_groups + g1[non_diag]
    aff.scatter_add_(0, flat_rev, weight[non_diag])
    aff = aff.view(num_groups, num_groups)

    # Start at the group with largest total connectivity.
    current = torch.argmax(aff.sum(dim=1))
    used = torch.zeros(num_groups, dtype=torch.bool, device=device)
    order = torch.empty(num_groups, dtype=torch.long, device=device)

    for i in range(num_groups):
        order[i] = current
        used[current] = True
        if i + 1 == num_groups:
            break
        score = aff[current].clone()
        score[used] = -torch.inf
        # If all remaining affinities are zero, prefer the remaining group with
        # the greatest total affinity to the already chosen set.
        if torch.max(score) <= 0:
            score = aff[:, order[: i + 1]].sum(dim=1)
            score[used] = -torch.inf
        current = torch.argmax(score)

    return order


def _induced_subgraph(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    vertices: torch.Tensor,
    num_vertices_global: int,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Build a local-index edge list induced by `vertices`, entirely on device."""
    device = src.device
    local_of_global = torch.full(
        (num_vertices_global,), -1, dtype=torch.long, device=device
    )
    local_of_global[vertices] = torch.arange(vertices.numel(), device=device)

    ls = local_of_global[src]
    ld = local_of_global[dst]
    keep = (ls >= 0) & (ld >= 0)
    return ls[keep], ld[keep], weight[keep]


@torch.no_grad()
def hierarchical_vertex_permutation(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    num_vertices: int,
    num_macro_parts: int,
    tile_size: int,
    *,
    macro_iterations: int = 8,
    micro_iterations: int = 6,
    candidate_swaps: int = 64,
    seed: Optional[int] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Create perm[new_index] = old_index.

    Stage 1: exactly balanced N-way graph partitioning.
    Stage 2: inside each macro partition, exactly balanced partitioning into
             groups of `tile_size` vertices.
    Stage 3: order macro/micro groups by connectivity so occupied tiles tend to
             stay spatially local.
    """
    if num_vertices % num_macro_parts != 0:
        raise ValueError("num_vertices must be divisible by num_macro_parts")
    macro_size = num_vertices // num_macro_parts
    if macro_size % tile_size != 0:
        raise ValueError("(num_vertices // num_macro_parts) must be divisible by tile_size")

    macro = balanced_partition_gpu(
        src,
        dst,
        weight,
        num_vertices,
        num_macro_parts,
        iterations=macro_iterations,
        candidate_swaps=candidate_swaps,
        seed=seed,
    )

    macro_order = _group_affinity_order(src, dst, weight, macro, num_macro_parts)
    chunks = []

    for macro_id_t in macro_order:
        macro_id = int(macro_id_t.item())
        verts = torch.nonzero(macro == macro_id, as_tuple=False).flatten()
        local_src, local_dst, local_w = _induced_subgraph(
            src, dst, weight, verts, num_vertices
        )

        num_micro = verts.numel() // tile_size
        if num_micro == 1:
            micro = torch.zeros(verts.numel(), dtype=torch.long, device=src.device)
            micro_order = torch.zeros(1, dtype=torch.long, device=src.device)
        else:
            micro_seed = None if seed is None else seed + 1009 * (macro_id + 1)
            micro = balanced_partition_gpu(
                local_src,
                local_dst,
                local_w,
                verts.numel(),
                num_micro,
                iterations=micro_iterations,
                candidate_swaps=min(candidate_swaps, tile_size),
                seed=micro_seed,
            )
            micro_order = _group_affinity_order(
                local_src, local_dst, local_w, micro, num_micro
            )

        # Each micro-group has exactly tile_size vertices and becomes one fixed
        # tile-aligned contiguous slot in the final permutation.
        local_degree = torch.zeros(verts.numel(), dtype=weight.dtype, device=src.device)
        if local_src.numel() > 0:
            local_degree.scatter_add_(0, local_src, local_w)
            non_diag = local_src != local_dst
            local_degree.scatter_add_(0, local_dst[non_diag], local_w[non_diag])

        for micro_id_t in micro_order:
            micro_id = int(micro_id_t.item())
            members = torch.nonzero(micro == micro_id, as_tuple=False).flatten()
            # Intra-tile ordering does not affect tile sparsity. Degree sorting is
            # a cheap locality-friendly tie-breaker.
            _, order = torch.sort(local_degree[members], descending=True)
            chunks.append(verts[members[order]])

    perm = torch.cat(chunks)
    if perm.numel() != num_vertices:
        raise RuntimeError("internal error: permutation has wrong length")
    return perm, macro


@torch.no_grad()
def extract_upper_tiles_from_edges(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    perm: torch.Tensor,
    tile_size: int,
    num_vertices: int,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """
    Apply the permutation logically and emit only nonzero upper-triangular tiles.

    If an undirected edge moves below the diagonal, it is transposed back via
    (min(new_u,new_v), max(new_u,new_v)).
    """
    device = src.device
    inv = torch.empty_like(perm)
    inv[perm] = torch.arange(num_vertices, device=device)

    ni = inv[src]
    nj = inv[dst]
    r = torch.minimum(ni, nj)
    c = torch.maximum(ni, nj)

    tr = torch.div(r, tile_size, rounding_mode="floor")
    tc = torch.div(c, tile_size, rounding_mode="floor")
    tiles_per_dim = (num_vertices + tile_size - 1) // tile_size
    tile_id = tr * tiles_per_dim + tc

    unique_ids, inverse = torch.unique(tile_id, sorted=True, return_inverse=True)
    num_tiles = unique_ids.numel()

    # Materialize only surviving tiles.
    out_dtype = weight.dtype
    tile_flat = torch.zeros(
        num_tiles * tile_size * tile_size,
        dtype=out_dtype,
        device=device,
    )
    lr = r.remainder(tile_size)
    lc = c.remainder(tile_size)
    flat_pos = inverse * (tile_size * tile_size) + lr * tile_size + lc
    tile_flat.scatter_add_(0, flat_pos, weight)
    tiles = tile_flat.view(num_tiles, tile_size, tile_size)

    coords = torch.stack(
        [
            torch.div(unique_ids, tiles_per_dim, rounding_mode="floor"),
            unique_ids.remainder(tiles_per_dim),
        ],
        dim=1,
    ).long()

    return tiles, coords, inv


@torch.no_grad()
def reorder_and_extract_block_coords_from_edges(
    src: torch.Tensor,
    dst: torch.Tensor,
    weight: torch.Tensor,
    num_vertices: int,
    num_partitions: int,
    tile_size: int,
    *,
    macro_iterations: int = 8,
    micro_iterations: int = 6,
    candidate_swaps: int = 64,
    seed: Optional[int] = None,
    score_by_magnitude: bool = True,
    require_cuda: bool = True,
) -> SparseBlockGraph:
    """Map a sparse edge list and emit occupied block coordinates only.

    ``src``, ``dst``, and ``weight`` have one entry per nonzero edge.  Directed
    duplicates are permitted: they affect partitioning affinity but are
    collapsed in the returned coordinate set.  Using absolute weights for the
    placement score is the default because communication cost is independent
    of an Ising coupling's sign.
    """
    _check_cuda_tensor(src, require_cuda)
    if src.ndim != 1 or dst.ndim != 1 or weight.ndim != 1:
        raise ValueError("src, dst, and weight must be one-dimensional")
    if not (src.numel() == dst.numel() == weight.numel()):
        raise ValueError("src, dst, and weight must have equal lengths")
    if src.device != dst.device or src.device != weight.device:
        raise ValueError("src, dst, and weight must be on the same device")
    if num_vertices <= 0 or num_partitions <= 0 or tile_size <= 0:
        raise ValueError("vertex, partition, and tile counts must be positive")
    if num_vertices % num_partitions:
        raise ValueError("num_vertices must be divisible by num_partitions")
    if (num_vertices // num_partitions) % tile_size:
        raise ValueError(
            "(num_vertices // num_partitions) must be divisible by tile_size"
        )
    if src.numel() and (
        bool(torch.any(src < 0)) or bool(torch.any(src >= num_vertices)) or
        bool(torch.any(dst < 0)) or bool(torch.any(dst >= num_vertices))
    ):
        raise ValueError("edge endpoint outside vertex range")

    lo = torch.minimum(src.long(), dst.long())
    hi = torch.maximum(src.long(), dst.long())
    score_weight = weight.float()
    if score_by_magnitude:
        score_weight = score_weight.abs()

    perm, macro = hierarchical_vertex_permutation(
        lo, hi, score_weight, num_vertices, num_partitions, tile_size,
        macro_iterations=macro_iterations,
        micro_iterations=micro_iterations,
        candidate_swaps=candidate_swaps,
        seed=seed,
    )
    inverse_perm = torch.empty_like(perm)
    inverse_perm[perm] = torch.arange(num_vertices, device=src.device)

    mapped_src = inverse_perm[lo]
    mapped_dst = inverse_perm[hi]
    block_a = torch.div(
        torch.minimum(mapped_src, mapped_dst), tile_size,
        rounding_mode="floor",
    )
    block_b = torch.div(
        torch.maximum(mapped_src, mapped_dst), tile_size,
        rounding_mode="floor",
    )
    blocks_per_dim = num_vertices // tile_size
    block_ids = block_a * blocks_per_dim + block_b
    unique_ids = torch.unique(block_ids, sorted=True)
    coords = torch.stack(
        [
            torch.div(unique_ids, blocks_per_dim, rounding_mode="floor"),
            unique_ids.remainder(blocks_per_dim),
        ],
        dim=1,
    ).long()
    return SparseBlockGraph(coords, perm, inverse_perm, macro)


@torch.no_grad()
def reorder_and_tile_upper_adjacency(
    adjacency: torch.Tensor,
    num_partitions: int,
    tile_size: int,
    *,
    macro_iterations: int = 8,
    micro_iterations: int = 6,
    candidate_swaps: int = 64,
    seed: Optional[int] = None,
    require_cuda: bool = True,
) -> TiledGraph:
    """
    Main entry point for a dense upper-triangular adjacency matrix.

    Parameters
    ----------
    adjacency:
        [V,V] upper-triangular adjacency matrix. Weighted graphs are supported.
    num_partitions:
        Number N of equal high-level partitions.
    tile_size:
        M in the fixed MxM tile grid.

    Returns
    -------
    TiledGraph with only nonzero upper-triangular tiles, tile coordinates,
    perm[new_index] = old_index, and inverse permutation.
    """
    _check_cuda_tensor(adjacency, require_cuda)
    if adjacency.ndim != 2 or adjacency.shape[0] != adjacency.shape[1]:
        raise ValueError("adjacency must be square")

    V = adjacency.shape[0]
    if V % num_partitions != 0:
        raise ValueError("V must be divisible by num_partitions")
    if (V // num_partitions) % tile_size != 0:
        raise ValueError("(V // num_partitions) must be divisible by tile_size")

    src, dst, weight = upper_triangular_to_edges(adjacency)
    # Integer/bool weights are inconvenient for gain arithmetic/scatter on some
    # devices, so use float scores while preserving values in final tiles.
    score_weight = weight
    if not score_weight.is_floating_point():
        score_weight = score_weight.float()

    perm, macro = hierarchical_vertex_permutation(
        src,
        dst,
        score_weight,
        V,
        num_partitions,
        tile_size,
        macro_iterations=macro_iterations,
        micro_iterations=micro_iterations,
        candidate_swaps=candidate_swaps,
        seed=seed,
    )

    tile_weight = weight
    if not tile_weight.is_floating_point():
        tile_weight = tile_weight.float()

    tiles, coords, inv = extract_upper_tiles_from_edges(
        src, dst, tile_weight, perm, tile_size, V
    )

    return TiledGraph(
        tiles=tiles,
        coords=coords,
        perm=perm,
        inverse_perm=inv,
        macro_labels=macro,
    )


if __name__ == "__main__":
    # Usage should place A on CUDA and leave
    # require_cuda=True.
    torch.manual_seed(7)
    V, N, M = 32, 4, 4
    A = torch.zeros(V, V)

    # Four synthetic communities, plus a few cross edges.
    for base in range(0, V, 8):
        block = torch.rand(8, 8) < 0.35
        block = torch.triu(block, diagonal=1)
        A[base : base + 8, base : base + 8] = block.float()
    for u, v in [(1, 10), (6, 18), (9, 26), (17, 27)]:
        A[min(u, v), max(u, v)] = 1.0

    result = reorder_and_tile_upper_adjacency(
        A,
        num_partitions=N,
        tile_size=M,
        seed=123,
        require_cuda=False,
    )

    assert torch.equal(torch.sort(result.perm).values, torch.arange(V))
    assert torch.all(result.coords[:, 0] <= result.coords[:, 1])
    print("perm:", result.perm.tolist())
    print("coords:", result.coords.tolist())
    print("tiles shape:", tuple(result.tiles.shape))
