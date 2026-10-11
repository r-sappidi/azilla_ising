import numpy as np
import sys
import matplotlib.pyplot as plt
import time

rng = np.random.default_rng()

def adj_list_size(adj_list, prec):
    if len(adj_list) == 0:
        return 0
    label_size = np.ceil(np.log2(len(adj_list)))
    mem = 0

    for key in list(adj_list):
        mem += label_size
        mem += (label_size + prec) * len(adj_list[key])
    return mem / 8

def dict_size(dict):
    size = 0
    for key in list(dict):
        size += len(dict[key])
    return size

def mat_compress(mat, prec):
    if np.sum(np.abs(mat)) == 0:
        return 0

    N = mat.shape[0]
    return (np.sum(mat != 0) * prec + N ** 2) / 8

def graph_compress(graph, fine, prec):
    mem = fine ** 2 / 8
    block_size = int(graph.shape[0] / fine)
    for i in range(fine):
        for j in range(fine):
            mem += mat_compress(graph[block_size * i: block_size * (i + 1), 
                                        block_size * j: block_size * (j + 1)], prec)
    return mem

def graph_compare(spr_num):
    x = []
    y = [[], [], []]
    spr = []
    for s in range(spr_num):
        spr.append([])

    for density in np.linspace(0, 1, 11):
        print(density)
        sparse = 1 - density
        N = 2 ** 12
        adj_mat = np.zeros((N, N), dtype=np.int8)
        adj_list = {}
        for i in range(N):
            adj_list[i] = []

        for i in range(N):
            for j in range(i, N):
                adj_mat[i][j] = 0 if (rng.random() < (sparse) or i == j) else 1
                if adj_mat[i][j] != 0:
                    adj_list[i].append((j, int(adj_mat[i][j])))
        
        for key in list(adj_list):
            if len(adj_list[key]) == 0:
                adj_list.pop(key)
        x.append(density)
        y[0].append((N - 1) * (N - 2) / 2)
        y[1].append(adj_list_size(adj_list, prec=8))
        y[2].append(np.sum(adj_mat))
        for s in range(spr_num):
            spr[s].append(graph_compress(adj_mat, 2 ** s, prec=8))

    print(time.time() - start)
    y = np.array(y)
    return x, y, spr

if __name__ == "__main__":
    start = time.time()
    spr_num = 12
    trials = 10
    x, y, spr = graph_compare(spr_num)
    plt.plot(x, y[0] / y[2])
    plt.plot(x, y[2] / y[2])

    legends = ["Adj Matrix", "Ideal Compression"]
    for s in range(spr_num):
        plt.plot(x, spr[s] / y[2])
        legends.append(f"Fine: {2 ** s}")
    plt.legend(legends)
    plt.show()
