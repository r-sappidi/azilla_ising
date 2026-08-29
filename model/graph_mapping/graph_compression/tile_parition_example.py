from tile_compressor import reorder_and_tile_upper_adjacency
import torch


### This code will not run, it is just an example on how to use the API
### A is adjacency matrix, N is num partions, in our case 16 bc 4x4 NoC
### MxM is the size of each matrix tile when we split the graph matrix
### Ensure num spins is divisible by N and (num spins / N) is divisible by M
### Ensure N is a square number
### perm[new_index] = old_index, inverse_perm[old_index] = new_index
### to bring back vertices to original names simply do:
### old_vertices = new_vertices[perm]

####    Example Use Case on GPU    #####
A = A.cuda() # move A to GPU

result = reorder_and_tile_upper_adjacency(
    A,
    num_partitions=8,
    tile_size=8,
)

print(result.tiles.shape)
print(result.coords.shape)
print(result.perm.shape)
####    Example GPU Example    #####




####    Example Use Case on CPU    #####
result = reorder_and_tile_upper_adjacency(
    adjacency=A,          # CUDA [V, V] upper-triangular tensor
    num_partitions=N,
    tile_size=M,
    seed=None,
)

tiles = result.tiles
coords = result.coords
perm = result.perm
inverse_perm = result.inverse_perm
####    End CPU Example    #####

