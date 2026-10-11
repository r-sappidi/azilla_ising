from tile_scheduler import schedule_tiles_on_mesh

### input tiles and coords from tile partition example into this method
### note this isn't runnable code just example use

schedule = schedule_tiles_on_mesh(
    tiles,          # [num_tiles, M, M]
    coords,         # [num_tiles, 2]
    num_cores=N,    # must be a perfect square
    pad=False,
)
