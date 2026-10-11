function [x_o] = H1_SB(x_i,J_i,N,a,b,c,decay,noise_stream,offset)

    num_tiles = 16;

    if mod(N,num_tiles) ~= 0
        error('N must be divisible by 16');
    end

    spins_per_tile = N/num_tiles;

    x_o = zeros(N,1);


    for tile = 1:num_tiles

        local_start = (tile-1)*spins_per_tile + 1;
        local_stop  = tile*spins_per_tile;


        global_offset = offset + local_start - 1;


        x_o(local_start:local_stop) = H0_SB( ...
            x_i, ...
            J_i(local_start:local_stop,:), ...
            spins_per_tile, ...
            a,b,c, ...
            decay, ...
            noise_stream(local_start:local_stop), ...
            global_offset);

    end

end