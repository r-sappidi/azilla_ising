
"""
tile_scheduler.py

Schedule nonzero MxM adjacency-matrix tiles onto a square 2-D mesh of accelerator
cores while favoring:

1. Strong local reuse within each core:
   tiles that touch the same vertex-block IDs are grouped together and ordered
   consecutively.

2. Weaker global/mesh locality:
   core workloads with similar vertex-block usage are placed on adjacent
   up/down/left/right cores.

The scheduler is written with PyTorch and works on CPU or CUDA tensors. The
assignment heuristic itself is GPU-friendly, while the final small N-core mesh
placement uses lightweight Python control flow because N is typically much
smaller than the number of graph tiles.

Coordinate convention
---------------------
coords[k] == [tile_row, tile_col]

A tile at [r, c] touches vertex-block IDs {r, c}. Therefore tiles [r, c] and
[r, d], for example, share one M-vertex block and have strong reuse potential.

Padding
-------
If pad=True, zero MxM tiles are appended until the total tile count is divisible
by num_cores. Padded coordinates are [-1, -1].

Returned data is arranged as [sqrtN][sqrtN], one entry per physical core.
Each entry is a tensor containing the sequence of tiles assigned to that core.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isqrt
from typing import List, Optional, Sequence, Tuple

import torch


@dataclass
class TileSchedule:
    """
    Result of schedule_tiles_on_mesh.

    Attributes
    ----------
    tiles_by_core:
        2-D Python list with shape [sqrtN][sqrtN].
        tiles_by_core[r][c] has shape [K_rc, M, M].

    coords_by_core:
        2-D Python list with shape [sqrtN][sqrtN].
        coords_by_core[r][c] has shape [K_rc, 2].
        Padding entries, if present, use coordinate [-1, -1].

    tile_indices_by_core:
        2-D Python list with shape [sqrtN][sqrtN].
        Original tile indices assigned to each core, in scheduled processing
        order. Padded tiles use index -1.

    padded:
        Whether padding was requested.

    num_padding_tiles:
        Number of zero tiles inserted.
    """
    tiles_by_core: List[List[torch.Tensor]]
    coords_by_core: List[List[torch.Tensor]]
    tile_indices_by_core: List[List[torch.Tensor]]
    padded: bool
    num_padding_tiles: int


def _validate_inputs(
    tiles: torch.Tensor,
    coords: torch.Tensor,
    num_cores: int,
) -> Tuple[int, int]:
    if tiles.ndim != 3:
        raise ValueError(
            f"tiles must have shape [num_tiles, M, M], got {tuple(tiles.shape)}"
        )
    if tiles.shape[1] != tiles.shape[2]:
        raise ValueError("tiles must contain square MxM matrices.")
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise ValueError(
            f"coords must have shape [num_tiles, 2], got {tuple(coords.shape)}"
        )
    if tiles.shape[0] != coords.shape[0]:
        raise ValueError(
            "tiles and coords must contain the same number of entries."
        )
    if tiles.device != coords.device:
        raise ValueError("tiles and coords must be on the same device.")
    if num_cores <= 0:
        raise ValueError("num_cores must be positive.")

    side = isqrt(num_cores)
    if side * side != num_cores:
        raise ValueError(
            f"num_cores must be a perfect square, got {num_cores}."
        )

    return tiles.shape[1], side


def _pad_tiles(
    tiles: torch.Tensor,
    coords: torch.Tensor,
    num_cores: int,
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, int]:
    """Pad to divisibility by num_cores. Padding indices are -1."""
    num_tiles = tiles.shape[0]
    pad_count = (-num_tiles) % num_cores

    original_indices = torch.arange(
        num_tiles, device=tiles.device, dtype=torch.long
    )

    if pad_count == 0:
        return tiles, coords, original_indices, 0

    M = tiles.shape[1]
    zero_tiles = torch.zeros(
        (pad_count, M, M), dtype=tiles.dtype, device=tiles.device
    )
    pad_coords = torch.full(
        (pad_count, 2), -1, dtype=coords.dtype, device=coords.device
    )
    pad_indices = torch.full(
        (pad_count,), -1, dtype=torch.long, device=tiles.device
    )

    return (
        torch.cat([tiles, zero_tiles], dim=0),
        torch.cat([coords, pad_coords], dim=0),
        torch.cat([original_indices, pad_indices], dim=0),
        pad_count,
    )


def _endpoint_histograms(
    coords: torch.Tensor,
    valid: torch.Tensor,
) -> Tuple[torch.Tensor, int]:
    """
    Build one sparse-ish dense histogram per tile over tile-coordinate endpoint
    IDs. Hist[t, b] is the number of times tile t touches vertex block b.

    Off-diagonal [r,c] contributes 1 to r and 1 to c.
    Diagonal [r,r] contributes 2 to r; this is intentional because that tile
    uses the same block on both matrix axes.
    """
    if valid.any():
        max_block = int(coords[valid].max().item())
        num_blocks = max_block + 1
    else:
        num_blocks = 1

    hist = torch.zeros(
        (coords.shape[0], num_blocks),
        dtype=torch.float32,
        device=coords.device,
    )

    valid_idx = torch.nonzero(valid, as_tuple=False).flatten()
    if valid_idx.numel():
        ones = torch.ones(valid_idx.numel(), device=coords.device)
        hist[valid_idx, coords[valid_idx, 0].long()] += ones
        hist[valid_idx, coords[valid_idx, 1].long()] += ones

    return hist, num_blocks


def _balanced_locality_groups(
    coords: torch.Tensor,
    original_indices: torch.Tensor,
    num_cores: int,
) -> List[List[int]]:
    """
    Greedy balanced clustering.

    The priority is to place each next tile on the core whose already-assigned
    tiles use the same vertex blocks most heavily. Capacity balancing is strict:
    target core sizes differ by at most one.

    Padded tiles are ignored here and appended after real tiles are grouped.
    """
    device = coords.device
    valid = original_indices >= 0
    real_idx = torch.nonzero(valid, as_tuple=False).flatten()
    num_real = int(real_idx.numel())

    if num_real == 0:
        return [[] for _ in range(num_cores)]

    tile_hist, num_blocks = _endpoint_histograms(coords, valid)

    base = num_real // num_cores
    extra = num_real % num_cores
    capacities = torch.tensor(
        [base + (1 if i < extra else 0) for i in range(num_cores)],
        dtype=torch.long,
        device=device,
    )

    # Prefer scheduling high-degree endpoint blocks first: tiles whose endpoint
    # blocks occur frequently are the best locality anchors.
    global_block_freq = tile_hist[real_idx].sum(dim=0)
    tile_anchor_score = (
        tile_hist[real_idx] * global_block_freq.unsqueeze(0)
    ).sum(dim=1)
    processing_order = real_idx[
        torch.argsort(tile_anchor_score, descending=True)
    ]

    group_hist = torch.zeros(
        (num_cores, num_blocks), dtype=torch.float32, device=device
    )
    group_sizes = torch.zeros(num_cores, dtype=torch.long, device=device)
    groups: List[List[int]] = [[] for _ in range(num_cores)]

    for tile_t in processing_order:
        t = int(tile_t.item())

        available = group_sizes < capacities
        candidates = torch.nonzero(available, as_tuple=False).flatten()

        # Main locality score: overlap between tile endpoints and cumulative
        # endpoint use on each core. Repeated reuse raises the score naturally.
        locality = group_hist[candidates] @ tile_hist[t]

        # Small fill preference only breaks locality ties; it prevents all seed
        # tiles from deterministically favoring low-index cores.
        remaining = capacities[candidates] - group_sizes[candidates]
        max_locality = locality.max()
        best_mask = locality == max_locality
        best_candidates = candidates[best_mask]
        best_remaining = remaining[best_mask]

        chosen = int(
            best_candidates[torch.argmax(best_remaining)].item()
        )

        groups[chosen].append(t)
        group_hist[chosen] += tile_hist[t]
        group_sizes[chosen] += 1

    return groups


def _order_group_for_locality(
    group: Sequence[int],
    tile_hist: torch.Tensor,
    coords: torch.Tensor,
) -> List[int]:
    """
    Order tiles inside one core using a greedy nearest-neighbor walk.

    Similarity = shared endpoint-block count. A secondary Manhattan coordinate
    term provides stable locality when endpoint overlap ties.
    """
    if len(group) <= 2:
        return list(group)

    remaining = list(group)

    # Start from the tile with strongest total endpoint overlap to the group.
    idx_t = torch.tensor(remaining, dtype=torch.long, device=coords.device)
    sim = tile_hist[idx_t] @ tile_hist[idx_t].T
    start_pos = int(sim.sum(dim=1).argmax().item())

    current = remaining.pop(start_pos)
    ordered = [current]

    while remaining:
        cand_t = torch.tensor(
            remaining, dtype=torch.long, device=coords.device
        )
        shared = tile_hist[cand_t] @ tile_hist[current]

        # Secondary preference for nearby tile-grid coordinates.
        delta = (
            coords[cand_t].to(torch.float32)
            - coords[current].to(torch.float32)
        ).abs().sum(dim=1)

        max_shared = shared.max()
        tied = torch.nonzero(
            shared == max_shared, as_tuple=False
        ).flatten()

        if tied.numel() > 1:
            pick_local = tied[torch.argmin(delta[tied])]
        else:
            pick_local = tied[0]

        pick = int(pick_local.item())
        current = remaining.pop(pick)
        ordered.append(current)

    return ordered


def _core_histograms(
    groups: Sequence[Sequence[int]],
    tile_hist: torch.Tensor,
) -> torch.Tensor:
    out = torch.zeros(
        (len(groups), tile_hist.shape[1]),
        dtype=torch.float32,
        device=tile_hist.device,
    )
    for i, g in enumerate(groups):
        if g:
            idx = torch.tensor(g, dtype=torch.long, device=tile_hist.device)
            out[i] = tile_hist[idx].sum(dim=0)
    return out


def _cosine_similarity_matrix(x: torch.Tensor) -> torch.Tensor:
    norm = torch.linalg.vector_norm(x, dim=1, keepdim=True).clamp_min(1e-12)
    xnorm = x / norm
    return xnorm @ xnorm.T


def _mesh_neighbors(pos: int, side: int) -> List[int]:
    r, c = divmod(pos, side)
    out = []
    if r > 0:
        out.append((r - 1) * side + c)
    if r + 1 < side:
        out.append((r + 1) * side + c)
    if c > 0:
        out.append(r * side + c - 1)
    if c + 1 < side:
        out.append(r * side + c + 1)
    return out


def _place_groups_on_mesh(
    groups: Sequence[Sequence[int]],
    core_hist: torch.Tensor,
    side: int,
) -> List[int]:
    """
    Return mesh_position -> group_id.

    High-affinity groups are greedily placed next to already placed groups.
    Only cardinal (up/down/left/right) adjacency contributes.
    """
    num_cores = side * side
    if num_cores == 1:
        return [0]

    similarity = _cosine_similarity_matrix(core_hist)

    # Strongest workload goes near the center so it has many cardinal neighbors.
    workload = core_hist.sum(dim=1)
    first_group = int(workload.argmax().item())

    # For even sides there are four central candidates; choose upper-left center.
    center_r = (side - 1) // 2
    center_c = (side - 1) // 2
    first_pos = center_r * side + center_c

    placement = [-1] * num_cores
    placement[first_pos] = first_group

    remaining_groups = set(range(num_cores))
    remaining_groups.remove(first_group)
    open_positions = set(range(num_cores))
    open_positions.remove(first_pos)

    while remaining_groups:
        best_score = None
        best_pair = None

        # Prefer frontier cells so every new placement is adjacent to existing
        # work whenever possible.
        frontier = [
            p for p in open_positions
            if any(placement[n] != -1 for n in _mesh_neighbors(p, side))
        ]
        candidate_positions = frontier if frontier else list(open_positions)

        for pos in candidate_positions:
            neighbor_groups = [
                placement[n]
                for n in _mesh_neighbors(pos, side)
                if placement[n] != -1
            ]

            for gid in remaining_groups:
                if neighbor_groups:
                    score = sum(
                        float(similarity[gid, ng].item())
                        for ng in neighbor_groups
                    )
                else:
                    score = 0.0

                # Tiny preference for heavier groups in otherwise exact ties.
                score += 1e-8 * float(workload[gid].item())

                if best_score is None or score > best_score:
                    best_score = score
                    best_pair = (pos, gid)

        assert best_pair is not None
        pos, gid = best_pair
        placement[pos] = gid
        open_positions.remove(pos)
        remaining_groups.remove(gid)

    return placement


def schedule_tiles_on_mesh(
    tiles: torch.Tensor,
    coords: torch.Tensor,
    num_cores: int,
    *,
    pad: bool = False,
) -> TileSchedule:
    """
    Schedule MxM graph tiles onto a square mesh of accelerator cores.

    Parameters
    ----------
    tiles:
        Tensor [T, M, M]. Usually the nonzero upper-triangular tiles returned by
        the graph-reordering stage.

    coords:
        Integer tensor [T, 2], where coords[k] = [tile_row, tile_col].

    num_cores:
        Number of physical accelerator cores. Must be a perfect square.

    pad:
        Default False.
        If True, append all-zero MxM tiles until T is divisible by num_cores.
        This makes every core receive exactly the same number of tile slots.
        Padding coordinates and original tile indices are -1.

        If False, no artificial work is inserted. Real-tile counts per core
        differ by at most one.

    Returns
    -------
    TileSchedule
        tiles_by_core[r][c]:
            [K_rc, M, M] tensor for physical core (r,c).

        coords_by_core[r][c]:
            [K_rc, 2] coordinates corresponding one-to-one with those tiles.

        tile_indices_by_core[r][c]:
            [K_rc] original indices into the input tile array. Padding = -1.

    Notes
    -----
    Locality interpretation:
      A tile [a,b] touches vertex-blocks a and b. Thus [a,b] and [a,c] share
      M vertices and are considered strongly related. This is the primary
      scheduling signal.

    Global locality:
      After constructing one workload per core, the core workloads are placed
      on the 2-D physical mesh so similar endpoint-block histograms are adjacent.
      Diagonal neighbors do not contribute.
    """
    M, side = _validate_inputs(tiles, coords, num_cores)

    if pad:
        work_tiles, work_coords, original_indices, pad_count = _pad_tiles(
            tiles, coords, num_cores
        )
    else:
        work_tiles = tiles
        work_coords = coords
        original_indices = torch.arange(
            tiles.shape[0], device=tiles.device, dtype=torch.long
        )
        pad_count = 0

    # Create groups from real tiles only.
    groups = _balanced_locality_groups(
        work_coords, original_indices, num_cores
    )

    valid = original_indices >= 0
    tile_hist, _ = _endpoint_histograms(work_coords, valid)

    # Improve temporal locality within each core.
    groups = [
        _order_group_for_locality(g, tile_hist, work_coords)
        for g in groups
    ]

    # If padded, distribute the padding evenly after real grouping.
    if pad and pad_count:
        pad_idx = torch.nonzero(
            original_indices < 0, as_tuple=False
        ).flatten().tolist()

        target = work_tiles.shape[0] // num_cores
        p = 0
        for gid in range(num_cores):
            need = target - len(groups[gid])
            if need > 0:
                groups[gid].extend(pad_idx[p:p + need])
                p += need

    # Build endpoint histograms from only real tiles for global locality.
    real_only_groups = [
        [t for t in g if int(original_indices[t].item()) >= 0]
        for g in groups
    ]
    core_hist = _core_histograms(real_only_groups, tile_hist)

    # Map abstract workload groups to physical 2-D core positions.
    mesh_to_group = _place_groups_on_mesh(
        real_only_groups, core_hist, side
    )

    tiles_by_core: List[List[torch.Tensor]] = []
    coords_by_core: List[List[torch.Tensor]] = []
    indices_by_core: List[List[torch.Tensor]] = []

    for r in range(side):
        tile_row = []
        coord_row = []
        index_row = []

        for c in range(side):
            pos = r * side + c
            gid = mesh_to_group[pos]
            g = groups[gid]

            if g:
                idx = torch.tensor(
                    g, dtype=torch.long, device=tiles.device
                )
                tile_row.append(work_tiles[idx])
                coord_row.append(work_coords[idx])
                index_row.append(original_indices[idx])
            else:
                tile_row.append(
                    torch.empty(
                        (0, M, M),
                        dtype=tiles.dtype,
                        device=tiles.device,
                    )
                )
                coord_row.append(
                    torch.empty(
                        (0, 2),
                        dtype=coords.dtype,
                        device=coords.device,
                    )
                )
                index_row.append(
                    torch.empty(
                        (0,),
                        dtype=torch.long,
                        device=tiles.device,
                    )
                )

        tiles_by_core.append(tile_row)
        coords_by_core.append(coord_row)
        indices_by_core.append(index_row)

    return TileSchedule(
        tiles_by_core=tiles_by_core,
        coords_by_core=coords_by_core,
        tile_indices_by_core=indices_by_core,
        padded=pad,
        num_padding_tiles=pad_count,
    )
